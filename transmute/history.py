"""Durable, prompt-toolkit-independent activity history.

The store deliberately opens a fresh SQLite connection for every operation.
That keeps connections confined to the calling thread while SQLite's WAL mode
and busy timeout coordinate Transmute's worker threads and other processes.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import STATE_DIR
from .downloader import Job

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5_000
_ACTIVE_STATUSES = ("queued", "downloading", "converting")
_INTERRUPTED = "interrupted — retry when ready"
_INTERRUPTED_DETAIL = "Transmute stopped before this job completed."


class HistoryStoreError(RuntimeError):
    """The activity database could not be safely opened or updated."""


@dataclass(frozen=True)
class StoredJob:
    """A persisted job and the UI state that accompanies it."""

    job: Job
    detail: str | None
    needs_hint: bool
    hint_attempts: int
    hint_in_progress: bool
    session_id: str
    created_at: datetime
    updated_at: datetime


class ActivityStore:
    """A versioned SQLite ledger of sessions and download outcomes."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self._job_claims: dict[str, tuple[str, str]] = {}
        self._job_claims_lock = threading.Lock()
        self._prepare_path()
        self._initialize()
        self._recover_interrupted()

    def start_session(self) -> str:
        """Create and return a session owned by this process."""
        session_id = uuid4().hex
        now = _utc_now()
        self._write(
            """
            INSERT INTO sessions (
                session_id, pid, hostname, started_at, finished_at
            ) VALUES (?, ?, ?, ?, NULL)
            """,
            (session_id, os.getpid(), socket.gethostname(), now),
            "start a history session",
        )
        return session_id

    def finish_session(self, session_id: str) -> None:
        """Finish a session and make any remaining work safely retryable."""
        now = _utc_now()
        with self._transaction("finish a history session") as connection:
            connection.execute(
                """
                UPDATE sessions
                SET finished_at = COALESCE(finished_at, ?)
                WHERE session_id = ?
                """,
                (now, session_id),
            )
            self._interrupt_jobs(connection, (session_id,), now)
            self._release_hint_claims(connection, (session_id,), now)
        with self._job_claims_lock:
            self._job_claims = {
                history_id: claim
                for history_id, claim in self._job_claims.items()
                if claim[0] != session_id
            }

    def queue_job(self, job: Job, session_id: str) -> bool:
        """Atomically claim one new/retry job for this session."""
        return job.history_id in self.queue_jobs([job], session_id)

    def queue_jobs(self, jobs: list[Job], session_id: str) -> set[str]:
        """Claim a batch in one transaction and return the claimed history IDs."""
        if not jobs:
            return set()
        now = _utc_now()
        claimed: dict[str, str] = {}
        with self._transaction("queue history jobs") as connection:
            self._assert_session(connection, session_id)
            for job in jobs:
                claim_token = uuid4().hex
                values = self._job_values(
                    job,
                    session_id=session_id,
                    status="queued",
                    error=None,
                    error_detail=None,
                    retryable=True,
                )
                cursor = connection.execute(
                    """
                    INSERT INTO jobs (
                        history_id, session_id, claim_token, url, status,
                        title, uploader, duration, description, tags_json, path,
                        error, error_detail, retryable, detail, needs_hint,
                        hint_attempts, hint_claim_token, hint_session_id,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, 0, 0, NULL, NULL, ?, ?
                    )
                    ON CONFLICT(history_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        claim_token = excluded.claim_token,
                        url = excluded.url,
                        status = excluded.status,
                        title = excluded.title,
                        uploader = excluded.uploader,
                        duration = excluded.duration,
                        description = excluded.description,
                        tags_json = excluded.tags_json,
                        path = excluded.path,
                        error = NULL,
                        error_detail = NULL,
                        retryable = 1,
                        detail = NULL,
                        needs_hint = 0,
                        hint_attempts = 0,
                        hint_claim_token = NULL,
                        hint_session_id = NULL,
                        updated_at = excluded.updated_at
                    WHERE jobs.status IN ('done', 'error')
                    """,
                    (
                        values[0],
                        values[1],
                        claim_token,
                        *values[2:],
                        now,
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    claimed[job.history_id] = claim_token

        with self._job_claims_lock:
            self._job_claims.update(
                {
                    history_id: (session_id, claim_token)
                    for history_id, claim_token in claimed.items()
                }
            )
        return set(claimed)

    def save_failure(self, job: Job) -> bool:
        """Save a failure only while this store still owns the job claim."""
        claim = self._job_claim(job.history_id)
        if claim is None:
            return False
        session_id, claim_token = claim
        now = _utc_now()
        with self._transaction("save a failed history job") as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET url = ?,
                    status = 'error',
                    title = ?,
                    uploader = ?,
                    duration = ?,
                    description = ?,
                    tags_json = ?,
                    path = ?,
                    error = ?,
                    error_detail = ?,
                    retryable = ?,
                    detail = NULL,
                    needs_hint = 0,
                    hint_attempts = 0,
                    claim_token = NULL,
                    hint_claim_token = NULL,
                    hint_session_id = NULL,
                    updated_at = ?
                WHERE history_id = ?
                  AND session_id = ?
                  AND claim_token = ?
                  AND status IN ('queued', 'downloading', 'converting')
                """,
                (
                    *self._job_metadata_values(job),
                    job.error,
                    job.error_detail,
                    int(job.retryable),
                    now,
                    job.history_id,
                    session_id,
                    claim_token,
                ),
            )
        self._release_job_claim(job.history_id, claim_token)
        return cursor.rowcount == 1

    def save_success(
        self,
        job: Job,
        detail: str | None,
        needs_hint: bool,
    ) -> bool:
        """Save success only while this store still owns the job claim."""
        claim = self._job_claim(job.history_id)
        if claim is None:
            return False
        session_id, claim_token = claim
        now = _utc_now()
        with self._transaction("save a successful history job") as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET url = ?,
                    status = 'done',
                    title = ?,
                    uploader = ?,
                    duration = ?,
                    description = ?,
                    tags_json = ?,
                    path = ?,
                    error = NULL,
                    error_detail = NULL,
                    retryable = 1,
                    detail = ?,
                    needs_hint = ?,
                    hint_attempts = 0,
                    claim_token = NULL,
                    hint_claim_token = NULL,
                    hint_session_id = NULL,
                    updated_at = ?
                WHERE history_id = ?
                  AND session_id = ?
                  AND claim_token = ?
                  AND status IN ('queued', 'downloading', 'converting')
                """,
                (
                    *self._job_metadata_values(job),
                    detail,
                    int(needs_hint),
                    now,
                    job.history_id,
                    session_id,
                    claim_token,
                ),
            )
        self._release_job_claim(job.history_id, claim_token)
        return cursor.rowcount == 1

    def claim_hint(self, job: Job, session_id: str) -> str | bool:
        """Claim one low-confidence re-hint without changing download status."""
        now = _utc_now()
        claim_token = uuid4().hex
        with self._transaction("claim a history hint") as connection:
            self._assert_session(connection, session_id)
            cursor = connection.execute(
                """
                UPDATE jobs
                SET hint_claim_token = ?,
                    hint_session_id = ?,
                    updated_at = ?
                WHERE history_id = ?
                  AND status = 'done'
                  AND needs_hint = 1
                  AND hint_claim_token IS NULL
                """,
                (claim_token, session_id, now, job.history_id),
            )
        return claim_token if cursor.rowcount == 1 else False

    def save_hint_success(
        self,
        job: Job,
        detail: str | None,
        needs_hint: bool,
        claim_token: str,
    ) -> bool:
        """Apply a re-hint only if its unique cross-process claim is current."""
        now = _utc_now()
        with self._transaction("save a successful history hint") as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET url = ?,
                    title = ?,
                    uploader = ?,
                    duration = ?,
                    description = ?,
                    tags_json = ?,
                    path = ?,
                    detail = ?,
                    needs_hint = ?,
                    hint_attempts = hint_attempts + 1,
                    hint_claim_token = NULL,
                    hint_session_id = NULL,
                    updated_at = ?
                WHERE history_id = ?
                  AND status = 'done'
                  AND hint_claim_token = ?
                """,
                (
                    *self._job_metadata_values(job),
                    detail,
                    int(needs_hint),
                    now,
                    job.history_id,
                    claim_token,
                ),
            )
        return cursor.rowcount == 1

    def release_hint(self, history_id: str, claim_token: str) -> bool:
        """Release a failed or canceled re-hint if its claim is still current."""
        now = _utc_now()
        with self._transaction("release a history hint") as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET hint_claim_token = NULL,
                    hint_session_id = NULL,
                    updated_at = ?
                WHERE history_id = ?
                  AND hint_claim_token = ?
                """,
                (now, history_id, claim_token),
            )
        return cursor.rowcount == 1

    def load_jobs(self, limit: int = 500) -> list[StoredJob]:
        """Load the most recently updated jobs, ordered oldest-to-newest."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        if limit == 0:
            return []

        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT *
                        FROM jobs
                        ORDER BY updated_at DESC, row_id DESC
                        LIMIT ?
                    )
                    ORDER BY updated_at ASC, row_id ASC
                    """,
                    (limit,),
                ).fetchall()
            return [self._stored_job(row) for row in rows]
        except HistoryStoreError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
            raise HistoryStoreError(
                f"Activity history at {self.path} contains malformed job data: {exc}"
            ) from exc

    def clear(self) -> None:
        """Delete terminal history while retaining work that is still in flight."""
        placeholders = ", ".join("?" for _ in _ACTIVE_STATUSES)
        self._write(
            f"DELETE FROM jobs WHERE status NOT IN ({placeholders})",
            _ACTIVE_STATUSES,
            "clear activity history",
        )

    def _prepare_path(self) -> None:
        try:
            parent_was_created = not self.path.parent.exists()
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if parent_was_created or (
                self.path.parent == STATE_DIR and not self.path.parent.is_symlink()
            ):
                self.path.parent.chmod(0o700)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            if not self.path.is_file():
                raise HistoryStoreError(
                    f"Activity history path is not a file: {self.path}"
                )
            self.path.chmod(0o600)
        except HistoryStoreError:
            raise
        except OSError as exc:
            raise HistoryStoreError(
                f"Could not secure activity history at {self.path}: {exc}"
            ) from exc

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    problem = check[0] if check else "no integrity result"
                    raise HistoryStoreError(
                        f"Activity history at {self.path} is corrupt: {problem}"
                    )

                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                        """
                    )
                }
                if version == 0 and not tables:
                    # Recheck after taking the write lock: another process may
                    # have initialized the empty file after our first read.
                    connection.execute("BEGIN IMMEDIATE")
                    locked_version = connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                    locked_tables = {
                        row[0]
                        for row in connection.execute(
                            """
                            SELECT name FROM sqlite_master
                            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                            """
                        )
                    }
                    if locked_version == 0 and not locked_tables:
                        self._create_schema(connection)
                    connection.commit()
                    version = connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                    tables = {
                        row[0]
                        for row in connection.execute(
                            """
                            SELECT name FROM sqlite_master
                            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                            """
                        )
                    }
                if version == 0:
                    raise HistoryStoreError(
                        f"Activity history at {self.path} has an unversioned schema; "
                        "the existing data was left untouched"
                    )
                elif version > SCHEMA_VERSION:
                    raise HistoryStoreError(
                        f"Activity history schema version {version} is newer than "
                        f"the supported version {SCHEMA_VERSION}; upgrade Transmute"
                    )
                elif version < SCHEMA_VERSION:
                    raise HistoryStoreError(
                        f"Activity history schema version {version} is unsupported"
                    )

                self._validate_schema(connection)
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if mode is None or str(mode[0]).lower() != "wal":
                    raise HistoryStoreError(
                        f"Could not enable WAL mode for activity history at {self.path}"
                    )
        except HistoryStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise HistoryStoreError(
                f"Could not open activity history at {self.path}: {exc}"
            ) from exc
        finally:
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                hostname TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE jobs (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                history_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                claim_token TEXT,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                uploader TEXT,
                duration INTEGER,
                description TEXT,
                tags_json TEXT,
                path TEXT,
                error TEXT,
                error_detail TEXT,
                retryable INTEGER NOT NULL,
                detail TEXT,
                needs_hint INTEGER NOT NULL DEFAULT 0,
                hint_attempts INTEGER NOT NULL DEFAULT 0,
                hint_claim_token TEXT,
                hint_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX jobs_updated_idx ON jobs(updated_at, row_id)"
        )
        connection.execute("CREATE INDEX jobs_session_idx ON jobs(session_id)")
        connection.execute(
            "CREATE INDEX jobs_hint_session_idx ON jobs(hint_session_id)"
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        required = {
            "sessions": {
                "session_id",
                "pid",
                "hostname",
                "started_at",
                "finished_at",
            },
            "jobs": {
                "row_id",
                "history_id",
                "session_id",
                "claim_token",
                "url",
                "status",
                "title",
                "uploader",
                "duration",
                "description",
                "tags_json",
                "path",
                "error",
                "error_detail",
                "retryable",
                "detail",
                "needs_hint",
                "hint_attempts",
                "hint_claim_token",
                "hint_session_id",
                "created_at",
                "updated_at",
            },
        }
        for table, expected_columns in required.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row[1] for row in rows}
            missing = expected_columns - columns
            if missing:
                names = ", ".join(sorted(missing))
                raise HistoryStoreError(
                    f"Activity history schema is malformed: "
                    f"{table} is missing {names}"
                )

    def _recover_interrupted(self) -> None:
        """Close dead sessions and turn their unfinished rows into failures."""
        try:
            with self._connection() as connection:
                sessions = connection.execute(
                    """
                    SELECT session_id, pid, hostname
                    FROM sessions
                    WHERE finished_at IS NULL
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise HistoryStoreError(
                f"Could not inspect activity history at {self.path}: {exc}"
            ) from exc

        hostname = socket.gethostname()
        stale = []
        for row in sessions:
            inactive_session = (
                row["hostname"] != hostname or not _process_is_alive(row["pid"])
            )
            if inactive_session:
                stale.append(row["session_id"])

        if not stale:
            return
        now = _utc_now()
        placeholders = ", ".join("?" for _ in stale)
        with self._transaction("recover interrupted history jobs") as connection:
            connection.execute(
                f"""
                UPDATE sessions
                SET finished_at = COALESCE(finished_at, ?)
                WHERE session_id IN ({placeholders})
                """,
                (now, *stale),
            )
            self._interrupt_jobs(connection, tuple(stale), now)
            self._release_hint_claims(connection, tuple(stale), now)

    @staticmethod
    def _interrupt_jobs(
        connection: sqlite3.Connection,
        session_ids: tuple[str, ...],
        now: str,
    ) -> None:
        if not session_ids:
            return
        session_placeholders = ", ".join("?" for _ in session_ids)
        status_placeholders = ", ".join("?" for _ in _ACTIVE_STATUSES)
        connection.execute(
            f"""
            UPDATE jobs
            SET status = 'error',
                error = ?,
                error_detail = ?,
                retryable = 1,
                detail = NULL,
                needs_hint = 0,
                claim_token = NULL,
                updated_at = ?
            WHERE session_id IN ({session_placeholders})
              AND status IN ({status_placeholders})
            """,
            (
                _INTERRUPTED,
                _INTERRUPTED_DETAIL,
                now,
                *session_ids,
                *_ACTIVE_STATUSES,
            ),
        )

    @staticmethod
    def _release_hint_claims(
        connection: sqlite3.Connection,
        session_ids: tuple[str, ...],
        now: str,
    ) -> None:
        if not session_ids:
            return
        placeholders = ", ".join("?" for _ in session_ids)
        connection.execute(
            f"""
            UPDATE jobs
            SET hint_claim_token = NULL,
                hint_session_id = NULL,
                updated_at = ?
            WHERE hint_session_id IN ({placeholders})
            """,
            (now, *session_ids),
        )

    @staticmethod
    def _assert_session(connection: sqlite3.Connection, session_id: str) -> None:
        row = connection.execute(
            "SELECT finished_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise HistoryStoreError(f"Unknown activity session: {session_id}")
        if row["finished_at"] is not None:
            raise HistoryStoreError(f"Activity session is already finished: {session_id}")

    def _job_claim(self, history_id: str) -> tuple[str, str] | None:
        with self._job_claims_lock:
            return self._job_claims.get(history_id)

    def _release_job_claim(self, history_id: str, claim_token: str) -> None:
        with self._job_claims_lock:
            claim = self._job_claims.get(history_id)
            if claim is not None and claim[1] == claim_token:
                del self._job_claims[history_id]

    @staticmethod
    def _job_metadata_values(job: Job) -> tuple[object, ...]:
        tags_json = (
            None
            if job.tags is None
            else json.dumps(job.tags, ensure_ascii=False, separators=(",", ":"))
        )
        return (
            job.url,
            job.title,
            job.uploader,
            job.duration,
            job.description,
            tags_json,
            str(job.path) if job.path is not None else None,
        )

    @staticmethod
    def _job_values(
        job: Job,
        *,
        session_id: str,
        status: str,
        error: str | None,
        error_detail: str | None,
        retryable: bool,
    ) -> tuple[object, ...]:
        metadata = ActivityStore._job_metadata_values(job)
        return (
            job.history_id,
            session_id,
            metadata[0],
            status,
            *metadata[1:],
            error,
            error_detail,
            int(retryable),
        )

    @staticmethod
    def _stored_job(row: sqlite3.Row) -> StoredJob:
        raw_tags = row["tags_json"]
        tags = None if raw_tags is None else json.loads(raw_tags)
        if tags is not None and (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) for tag in tags)
        ):
            raise ValueError("tags_json is not a list of strings")
        job = Job(
            url=row["url"],
            status=row["status"],
            title=row["title"],
            uploader=row["uploader"],
            duration=row["duration"],
            description=row["description"],
            tags=tags,
            path=Path(row["path"]) if row["path"] is not None else None,
            error=row["error"],
            error_detail=row["error_detail"],
            retryable=bool(row["retryable"]),
            history_id=row["history_id"],
        )
        return StoredJob(
            job=job,
            detail=row["detail"],
            needs_hint=bool(row["needs_hint"]),
            hint_attempts=row["hint_attempts"],
            hint_in_progress=row["hint_claim_token"] is not None,
            session_id=row["session_id"],
            created_at=_parse_utc(row["created_at"]),
            updated_at=_parse_utc(row["updated_at"]),
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=BUSY_TIMEOUT_MS / 1_000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = NORMAL")
            yield connection
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(self, action: str) -> Iterator[sqlite3.Connection]:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except HistoryStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise HistoryStoreError(
                f"Could not {action} in {self.path}: {exc}"
            ) from exc

    def _write(
        self,
        sql: str,
        parameters: tuple[object, ...],
        action: str,
    ) -> None:
        with self._transaction(action) as connection:
            connection.execute(sql, parameters)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp is missing a UTC offset")
    return parsed.astimezone(timezone.utc)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
