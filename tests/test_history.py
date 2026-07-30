import os
import sqlite3
import stat
from datetime import timezone
from pathlib import Path

import pytest

from transmute.downloader import Job
from transmute.history import ActivityStore, HistoryStoreError


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "state" / "activity.sqlite3"


@pytest.fixture
def store(store_path):
    return ActivityStore(store_path)


def test_job_round_trip_preserves_download_and_ui_fields(store):
    session_id = store.start_session()
    job = Job(
        url="https://soundcloud.com/artist/track",
        title="Títle",
        uploader="Artist",
        duration=213,
        description="source description",
        tags=["electronic", "日本語"],
        path=Path("/tmp/Artist - Track.mp3"),
    )

    store.queue_job(job, session_id)
    job.status = "done"
    store.save_success(job, "Artist — Track (2026)", needs_hint=True)

    [record] = store.load_jobs()
    assert record.job == job
    assert record.job.history_id == job.history_id
    assert record.job.path == Path("/tmp/Artist - Track.mp3")
    assert record.job.tags == ["electronic", "日本語"]
    assert record.detail == "Artist — Track (2026)"
    assert record.needs_hint
    assert record.hint_attempts == 0
    assert record.session_id == session_id
    assert record.created_at.tzinfo == timezone.utc
    assert record.updated_at.tzinfo == timezone.utc
    assert record.updated_at >= record.created_at


def test_successful_rehints_increment_and_requeue_resets_state(store):
    first_session = store.start_session()
    job = Job(url="https://youtu.be/example")
    store.queue_job(job, first_session)
    store.save_success(job, "first", needs_hint=True)
    second_claim = store.claim_hint(job, first_session)
    assert isinstance(second_claim, str)
    store.save_hint_success(
        job,
        "second",
        needs_hint=True,
        claim_token=second_claim,
    )
    third_claim = store.claim_hint(job, first_session)
    assert isinstance(third_claim, str)
    store.save_hint_success(
        job,
        "third",
        needs_hint=False,
        claim_token=third_claim,
    )

    [record] = store.load_jobs()
    assert record.detail == "third"
    assert not record.needs_hint
    assert record.hint_attempts == 2

    second_session = store.start_session()
    job.error = "stale error"
    job.error_detail = "stale detail"
    store.queue_job(job, second_session)

    [record] = store.load_jobs()
    assert record.job.status == "queued"
    assert record.job.error is None
    assert record.job.error_detail is None
    assert record.job.retryable
    assert record.detail is None
    assert not record.needs_hint
    assert record.hint_attempts == 0
    assert record.session_id == second_session


def test_only_one_store_can_claim_the_same_retry(store_path):
    first = ActivityStore(store_path)
    first_session = first.start_session()
    job = Job(url="https://youtu.be/retry")
    assert first.queue_job(job, first_session)
    job.error = "network error"
    first.save_failure(job)

    second = ActivityStore(store_path)
    second_session = second.start_session()
    restored_job = second.load_jobs()[0].job

    assert first.queue_job(job, first_session)
    assert not second.queue_job(restored_job, second_session)
    [record] = second.load_jobs()
    assert record.job.status == "queued"
    assert record.session_id == first_session


@pytest.mark.parametrize("terminal_state", ["done", "error"])
def test_late_worker_cannot_overwrite_a_newer_retry_claim(
    store_path, terminal_state
):
    first = ActivityStore(store_path)
    first_session = first.start_session()
    stale_job = Job(url="https://youtu.be/stale-worker")
    assert first.queue_job(stale_job, first_session)

    # Finishing turns the first attempt into a retryable failure and revokes
    # its unique claim, even if its worker thread has not returned yet.
    first.finish_session(first_session)
    second = ActivityStore(store_path)
    second_session = second.start_session()
    retry = second.load_jobs()[0].job
    assert second.queue_job(retry, second_session)

    stale_job.status = terminal_state
    if terminal_state == "done":
        saved = first.save_success(stale_job, "stale success", needs_hint=False)
    else:
        stale_job.error = "stale failure"
        saved = first.save_failure(stale_job)

    assert not saved
    [record] = second.load_jobs()
    assert record.job.status == "queued"
    assert record.session_id == second_session
    assert record.detail is None
    assert record.job.error is None


def test_only_one_store_can_claim_a_hint_and_stale_result_is_rejected(store_path):
    seed = ActivityStore(store_path)
    seed_session = seed.start_session()
    job = Job(url="https://youtu.be/needs-hint")
    assert seed.queue_job(job, seed_session)
    assert seed.save_success(job, "original", needs_hint=True)
    seed.finish_session(seed_session)

    first = ActivityStore(store_path)
    first_session = first.start_session()
    second = ActivityStore(store_path)
    second_session = second.start_session()
    first_job = first.load_jobs()[0].job
    second_job = second.load_jobs()[0].job

    first_claim = first.claim_hint(first_job, first_session)
    assert isinstance(first_claim, str)
    assert second.claim_hint(second_job, second_session) is False

    # Closing the first REPL releases its hint claim. A second REPL can then
    # claim it, and the first provider result cannot overwrite the new owner.
    first.finish_session(first_session)
    second_claim = second.claim_hint(second_job, second_session)
    assert isinstance(second_claim, str)
    assert second.save_hint_success(
        second_job,
        "current result",
        needs_hint=False,
        claim_token=second_claim,
    )
    assert not first.save_hint_success(
        first_job,
        "stale result",
        needs_hint=True,
        claim_token=first_claim,
    )

    [record] = second.load_jobs()
    assert record.detail == "current result"
    assert not record.needs_hint
    assert record.hint_attempts == 1
    assert not record.hint_in_progress


def test_queue_jobs_claims_a_batch_without_stealing_active_rows(store_path):
    first = ActivityStore(store_path)
    first_session = first.start_session()
    active = Job(url="https://youtu.be/active")
    assert first.queue_job(active, first_session)

    second = ActivityStore(store_path)
    second_session = second.start_session()
    new_jobs = [Job(url=f"https://youtu.be/new-{index}") for index in range(3)]
    claimed = second.queue_jobs([active, *new_jobs], second_session)

    assert claimed == {job.history_id for job in new_jobs}
    records = {
        record.job.history_id: record for record in second.load_jobs()
    }
    assert records[active.history_id].session_id == first_session
    assert all(records[job.history_id].session_id == second_session for job in new_jobs)


def test_failure_round_trip(store):
    session_id = store.start_session()
    job = Job(url="https://youtu.be/missing")
    store.queue_job(job, session_id)
    job.status = "error"
    job.title = "Unavailable"
    job.error = "video unavailable"
    job.error_detail = "ERROR: Video unavailable"
    job.retryable = False
    store.save_failure(job)

    [record] = store.load_jobs()
    assert record.job.status == "error"
    assert record.job.error == "video unavailable"
    assert record.job.error_detail == "ERROR: Video unavailable"
    assert not record.job.retryable
    assert record.detail is None
    assert not record.needs_hint


def test_clear_removes_terminal_rows_but_preserves_in_flight_work(store):
    session_id = store.start_session()
    done = Job(url="https://youtu.be/done")
    failed = Job(url="https://youtu.be/failed")
    queued = Job(url="https://youtu.be/queued")
    for job in (done, failed, queued):
        store.queue_job(job, session_id)
    store.save_success(done, None, needs_hint=False)
    failed.error = "network error"
    store.save_failure(failed)

    store.clear()

    records = store.load_jobs()
    assert [record.job.history_id for record in records] == [queued.history_id]

    # A worker that completes after /clear still has a durable outcome.
    store.save_success(queued, "late result", needs_hint=False)
    [record] = store.load_jobs()
    assert record.job.status == "done"
    assert record.detail == "late result"


def test_finish_session_recovers_queued_job_as_retryable_failure(store):
    session_id = store.start_session()
    job = Job(url="https://youtu.be/interrupted")
    store.queue_job(job, session_id)

    store.finish_session(session_id)
    store.finish_session(session_id)  # finishing is idempotent

    [record] = store.load_jobs()
    assert record.job.status == "error"
    assert record.job.retryable
    assert "interrupted" in record.job.error


def test_finished_sessions_are_not_reprocessed_on_every_startup(
    store_path, monkeypatch
):
    first_store = ActivityStore(store_path)
    session_id = first_store.start_session()
    first_store.finish_session(session_id)

    def unexpected_process_check(_pid):
        raise AssertionError("finished sessions should not be checked again")

    monkeypatch.setattr(
        "transmute.history._process_is_alive",
        unexpected_process_check,
    )
    ActivityStore(store_path)


def test_startup_recovers_dead_process_but_preserves_live_session(store_path):
    first_store = ActivityStore(store_path)
    live_session = first_store.start_session()
    live_job = Job(url="https://youtu.be/live")
    first_store.queue_job(live_job, live_session)

    second_store = ActivityStore(store_path)
    [record] = second_store.load_jobs()
    assert record.job.status == "queued"

    with sqlite3.connect(store_path) as connection:
        connection.execute(
            "UPDATE sessions SET pid = ? WHERE session_id = ?",
            (99_999_999, live_session),
        )

    recovered_store = ActivityStore(store_path)
    [record] = recovered_store.load_jobs()
    assert record.job.status == "error"
    assert record.job.retryable
    assert record.job.error == "interrupted — retry when ready"


def test_startup_recovers_unfinished_session_from_another_host(store_path):
    first_store = ActivityStore(store_path)
    session_id = first_store.start_session()
    job = Job(url="https://youtu.be/other-host")
    first_store.queue_job(job, session_id)

    with sqlite3.connect(store_path) as connection:
        connection.execute(
            "UPDATE sessions SET hostname = ? WHERE session_id = ?",
            ("a-different-host", session_id),
        )

    recovered_store = ActivityStore(store_path)
    [record] = recovered_store.load_jobs()
    assert record.job.status == "error"
    assert record.job.retryable


def test_recent_limit_is_ordered_oldest_to_newest_by_last_update(store):
    session_id = store.start_session()
    jobs = [Job(url=f"https://youtu.be/{number}") for number in range(3)]
    for job in jobs:
        store.queue_job(job, session_id)
        store.save_success(job, str(job.url), needs_hint=False)

    # Updating the oldest stable job moves it to the newest history position.
    store.queue_job(jobs[0], session_id)
    store.save_success(jobs[0], "retried", needs_hint=False)

    records = store.load_jobs(limit=2)
    assert [record.job.history_id for record in records] == [
        jobs[2].history_id,
        jobs[0].history_id,
    ]
    assert store.load_jobs(limit=0) == []
    with pytest.raises(ValueError):
        store.load_jobs(limit=-1)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_database_and_state_directory_are_private(store_path):
    ActivityStore(store_path)

    directory_mode = stat.S_IMODE(store_path.parent.stat().st_mode)
    file_mode = stat.S_IMODE(store_path.stat().st_mode)
    assert directory_mode == 0o700
    assert file_mode == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_existing_custom_parent_permissions_are_not_changed(tmp_path):
    custom_parent = tmp_path / "shared"
    custom_parent.mkdir(mode=0o755)
    custom_parent.chmod(0o755)
    path = custom_parent / "activity.sqlite3"

    ActivityStore(path)

    assert stat.S_IMODE(custom_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_newer_or_malformed_schema_raises_without_deleting_data(store_path):
    store_path.parent.mkdir()
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE keep_me (value TEXT)")
        connection.execute("INSERT INTO keep_me VALUES ('untouched')")
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(HistoryStoreError, match="newer"):
        ActivityStore(store_path)

    with sqlite3.connect(store_path) as connection:
        assert connection.execute("SELECT value FROM keep_me").fetchone()[0] == "untouched"


def test_corrupt_database_raises_without_replacing_file(store_path):
    store_path.parent.mkdir()
    original = b"this is not sqlite"
    store_path.write_bytes(original)

    with pytest.raises(HistoryStoreError, match="activity history"):
        ActivityStore(store_path)

    assert store_path.read_bytes() == original
