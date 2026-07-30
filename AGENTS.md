# Transmute maintainer guide

Transmute is a local Python REPL that turns YouTube or SoundCloud links into
tagged MP3s. It uses `prompt_toolkit` for the interface, `yt-dlp` and ffmpeg for
media processing, and a user-selected Claude, Codex, OpenAI, or Anthropic
credential for metadata research.

This file is the canonical repository contract for coding agents and human
contributors. `CLAUDE.md` must remain a relative symlink to `AGENTS.md`. Edit
this file, not the symlink. Rule identifiers are stable review shorthand; add,
remove, or renumber them only when the underlying standard changes.

## 1. Repository workflow

- **R0 — Read this guide first.** Read the complete `AGENTS.md` before planning,
  editing, reviewing, or running destructive commands in this repository.
- **R1 — Work in the assigned workspace.** Make changes from the current
  Conductor workspace, not the root checkout or a sibling worktree.
- **R2 — Keep the current branch.** Do not rename or switch the workspace branch
  unless the user explicitly requests it.
- **R3 — Compare against the target branch.** Use `origin/main` as the base for
  diffs, reviews, and pull requests.
- **R4 — Inspect before editing.** Read the affected implementation, tests,
  documentation, and current `git status` before changing files.
- **R5 — Preserve existing work.** Treat pre-existing modifications and
  untracked files as user-owned unless the task clearly created them.
- **R6 — Keep scratch work untracked.** Put temporary research or coordination
  artifacts in `.context/`; do not commit them.
- **R7 — Use repository tools.** Prefer `uv`, Ruff, pytest, and Justfile tasks
  already selected by the project over introducing equivalent tooling.
- **R8 — Avoid destructive recovery.** Do not use `git reset --hard`, broad
  recursive deletion, or checkout commands that discard work.
- **R9 — Limit scope.** Make only changes required for the requested outcome and
  its tests or documentation.
- **R10 — Report the final state.** State what changed, which checks ran, and any
  remaining risk or manual validation in the handoff.

## 2. Development commands

Run every command from the repository root.

- **D1 — Install dependencies:** `uv sync`
- **D2 — Run the application:** `uv run transmute`
- **D3 — Run the complete test suite:** `uv run pytest`
- **D4 — Run one test module:** `uv run pytest tests/test_app.py`
- **D5 — Run one test:** `uv run pytest tests/test_app.py::test_name`
- **D6 — Run lint:** `uv run ruff check .`
- **D7 — Run the complete local gate:** `just check`
- **D8 — Handle missing Just explicitly.** If `just` is unavailable, run
  `uv run pytest` and `uv run ruff check .` separately.
- **D9 — Test narrowly during iteration.** Start with the smallest relevant test
  target so failures remain attributable.
- **D10 — Finish broadly.** Run the complete local gate before handoff unless an
  external dependency makes it impossible; disclose any skipped check.
- **D11 — Regenerate the Homebrew formula:** `just formula`. Required whenever a
  runtime dependency, `uv.lock`, or `__version__` changes; the formula is
  derived from the lockfile and must not be hand-edited.

## 3. Product invariants

- **P1 — Keep media processing local.** Downloaded audio, thumbnails, and tagged
  files must stay on the user's machine.
- **P2 — Do not add remote media processing incidentally.** An upload service,
  hosted worker, or cloud conversion path requires an explicit product decision.
- **P3 — Keep playlist expansion disabled.** Preserve `noplaylist=True` in the
  yt-dlp configuration.
- **P4 — Treat playlist support as a feature.** It requires explicit queue
  limits, progress semantics, cancellation behavior, and state-machine tests.
- **P5 — Preserve supported input behavior.** Free text may contain one URL,
  several URLs, or supported URLs pasted back-to-back.
- **P6 — Keep the input responsive.** Downloads and enrichment must not block
  the prompt-toolkit event loop.
- **P7 — Preserve bounded concurrency.** Submit background work through the
  configured executor and respect `MAX_WORKERS`.
- **P8 — Preserve retry behavior.** Failed jobs must remain actionable and must
  be retryable without duplicating successful jobs.
- **P9 — Preserve confidence behavior.** Low-confidence artist attribution must
  stay visible and accept a user hint for re-research.
- **P10 — Preserve cover art.** Metadata updates and renames must not discard
  artwork already embedded by yt-dlp.
- **P11 — Never overwrite silently.** A rename or future output-template change
  must not replace an existing media file without an explicit policy.
- **P12 — Keep defaults honest.** The status bar, help, README, and actual
  settings must agree about output directory, quality, providers, and commands.

## 4. Credentials, privacy, and subprocess security

- **S1 — Never persist an entered API key.** A key supplied through `/key` stays
  in memory only.
- **S2 — Never echo an API key.** Do not place a key in messages, errors, logs,
  tracebacks, command output, or test snapshots.
- **S3 — Never retain a key in history.** `/key` handling must strip pasted
  secrets before prompt history is written.
- **S4 — Preserve credential precedence.** An entered key takes precedence over
  environment keys and subscription-backed CLIs.
- **S5 — Preserve environment precedence.** When both environment keys exist,
  `OPENAI_API_KEY` takes precedence over `ANTHROPIC_API_KEY`.
- **S6 — Keep environment API usage explicit.** Environment keys select
  separately billed API backends; they are not subscription credentials.
- **S7 — Preserve subscription fallback.** With no API key, prefer the installed
  Claude CLI and then the installed Codex CLI.
- **S8 — Keep missing credentials non-fatal.** If no backend is usable,
  enrichment is skipped with an actionable message; downloading still works.
- **S9 — Never run `/logout` during validation.** This applies to automated
  tests, manual checks, examples, and exploratory commands.
- **S10 — Protect machine-wide auth.** Do not delete or rewrite Claude Code,
  Codex, or Conductor credential files.
- **S11 — Isolate headless Claude.** Do not inherit parent Claude session
  markers or project context into the metadata subprocess.
- **S12 — Isolate headless Codex.** Keep the lookup ephemeral, read-only,
  shell-disabled, approval-free, and detached from the parent agent session.
- **S13 — Use a neutral subprocess directory.** Metadata research must not ingest
  repository instructions or unrelated local files through its working directory.
- **S14 — Filter inherited agent state.** Preserve the user's normal runtime and
  authentication environment while removing variables that would nest the
  lookup inside the parent Claude or Codex session.
- **S15 — Bound subprocess runtime.** Every provider CLI invocation must have an
  explicit timeout and a clear timeout error.
- **S16 — Bound provider errors.** Convert third-party failures into concise,
  single-purpose user messages without secrets or unbounded response bodies.
- **S17 — Do not loosen security silently.** Changes to CLI flags, tools,
  sandboxing, working directories, or inherited environment require focused
  security review and regression tests.
- **S18 — Keep tests credential-neutral.** Tests must clear or monkeypatch
  provider environment variables before constructing `Enricher` or `App`.

## 5. Architecture and dependency direction

The dependency direction is:

```text
main
  └── App (state + orchestration)
        ├── prompt_toolkit adapters (layout, keys, commands, widgets, style)
        ├── downloader (yt-dlp/ffmpeg)
        └── enrich (provider calls + normalized tags + ID3 writes)
```

- **A1 — Keep `main.py` thin.** It owns process startup and delegates application
  behavior to `App`.
- **A2 — Keep `app.py` authoritative.** It owns session state, job lifecycle,
  worker coordination, and UI-facing state helpers.
- **A3 — Keep `layout.py` structural.** It owns prompt-toolkit buffers, windows,
  containers, focus wiring, and layout composition.
- **A4 — Keep `keys.py` behavioral.** It maps keystrokes to existing `App`
  operations and must not perform downloads, provider calls, or file writes.
- **A5 — Keep `commands.py` command-focused.** It parses slash commands,
  validates their arguments, and delegates state or long-running work.
- **A6 — Keep `widgets.py` reusable.** Its controls and processors must not
  depend on Transmute session state when a callback or value provider will do.
- **A7 — Keep `style.py` presentational.** It owns visual constants and
  user-facing display text that is truly presentation-level.
- **A8 — Keep `config.py` small.** It owns stable settings and operational
  constants, not orchestration or mutable global session state.
- **A9 — Keep `downloader.py` UI-independent.** It must be importable and usable
  without `prompt_toolkit`.
- **A10 — Keep `enrich.py` UI-independent.** It must be importable and usable
  without `prompt_toolkit`.
- **A11 — Keep `Job` a service model.** Download status and source metadata
  belong to the download domain, not to a window or formatted text fragment.
- **A12 — Keep `TrackTags` normalized.** Every enrichment backend must produce
  the same provider-neutral tag model.
- **A13 — Pass data across boundaries.** Services accept values, dataclasses,
  settings, and callbacks—not prompt-toolkit buffers, windows, or `App`.
- **A14 — Return data from services.** Services must not render UI fragments or
  write directly into application history.
- **A15 — Keep UI text out of provider transports.** Provider code returns data
  or errors; `App` and commands decide how users see them.
- **A16 — Avoid circular imports.** Move a shared contract to the narrowest
  neutral module instead of importing application modules back into services.
- **A17 — Avoid a generic `utils.py`.** Put helpers beside the domain that owns
  their invariants and vocabulary.
- **A18 — Avoid premature type modules.** Keep a dataclass near its domain until
  several independent modules need a neutral shared contract.
- **A19 — Avoid import-time work.** Module import must not access the network,
  inspect credentials destructively, start threads, or modify the filesystem.
- **A20 — Keep heavy integrations lazy where practical.** Import provider SDKs
  and yt-dlp close to the operation that needs them so startup and tests stay
  isolated.
- **A21 — Add abstractions for demonstrated variation.** A protocol, registry,
  or base class needs at least two real implementations or a concrete testing
  boundary.
- **A22 — Do not build a plugin platform preemptively.** Third-party discovery,
  package namespaces, and lifecycle hooks wait for an explicit extension use
  case.

## 6. Application state and concurrency

- **C1 — Give session state one owner.** `App` is the source of truth for
  history, selection, queued work, active work, completion, failure, and modals.
- **C2 — Do not duplicate state in UI modules.** Layout, keys, commands, and
  widgets may read or request state but must not introduce a second store.
- **C3 — Protect compound shared access.** Use `App.lock` when a renderer and a
  worker can concurrently read, iterate, or mutate the same state.
- **C4 — Snapshot under the lock.** Copy the values needed for rendering while
  locked, then release the lock before formatting.
- **C5 — Keep critical sections short.** Do not perform formatting, callbacks,
  or unrelated computation while holding `App.lock`.
- **C6 — Never hold the lock during I/O.** Network, yt-dlp, ffmpeg, model,
  subprocess, and filesystem operations run outside the lock.
- **C7 — Never hold the lock while refreshing.** Complete the mutation, release
  the lock, and then call `refresh()`.
- **C8 — Publish worker changes through helpers.** A worker must use an `App`
  method that applies the required locking and invalidates the interface.
- **C9 — Keep rendering side-effect-free.** Render callbacks may snapshot and
  format state; they must not submit jobs, mutate selection, or write files.
- **C10 — Keep rendering fast.** Render callbacks must not block, sleep, spawn,
  access the network, or scan the filesystem.
- **C11 — Preserve the queue transition.** Submission increments queued work
  before worker execution begins.
- **C12 — Preserve the active transition.** A worker atomically moves one job
  from queued to active.
- **C13 — Preserve terminal transitions.** A job finishes in exactly one
  completed or failed outcome.
- **C14 — Clean up active state in `finally`.** Exceptions and early returns
  must not leave stale processing rows.
- **C15 — Keep sequence identifiers unique.** Active rows use monotonic IDs so
  equal URLs or titles cannot collide.
- **C16 — Keep progress callbacks lightweight.** They update status and refresh;
  they do not perform enrichment or extra media inspection.
- **C17 — Keep modal focus coherent.** Opening, closing, selection changes, and
  hint submission must leave focus on the correct buffer.
- **C18 — Drain intentionally on shutdown.** After the TUI exits, in-flight work
  may finish and must produce accurate final counts.
- **C19 — Do not silently abandon work.** Cancellation or forced abandonment
  needs an explicit user action and defined state semantics.
- **C20 — Avoid timing-based correctness.** Tests and production state
  transitions must not depend on arbitrary sleeps.

## 7. Download and media pipeline

- **DL1 — Use yt-dlp as the extraction boundary.** Prefer its supported options,
  extractors, hooks, and postprocessors over site-specific code in Transmute.
- **DL2 — Do not copy upstream extractors.** If upstream behavior is missing,
  prefer an upstream fix or a narrowly justified adapter.
- **DL3 — Request audio explicitly.** Keep the format selection audio-focused and
  let ffmpeg perform the supported conversion.
- **DL4 — Keep post-processing explicit.** Audio extraction, metadata copying,
  and thumbnail embedding remain visible in yt-dlp options.
- **DL5 — Keep progress optional.** `download_job` must work without a progress
  callback.
- **DL6 — Keep callbacks provider-neutral.** Progress reports a `Job` and a
  fraction or phase marker, not prompt-toolkit fragments.
- **DL7 — Preserve source metadata.** Capture title, uploader, duration,
  description, and page tags when yt-dlp provides them.
- **DL8 — Preserve the output path.** Record the actual postprocessed file path
  returned by yt-dlp rather than reconstructing it from a template.
- **DL9 — Create the output directory safely.** Use `Path.mkdir` with parent
  creation and surface an actionable filesystem error.
- **DL10 — Keep URL extraction deterministic.** Parsing must preserve input order
  and must not manufacture or expand URLs.
- **DL11 — Convert third-party exceptions at the boundary.** A download failure
  sets `Job.status`, stores a bounded message, and returns control to the app.
- **DL12 — Do not hide partial files carelessly.** Any future cleanup policy must
  distinguish safe temporary artifacts from a user's pre-existing files.
- **DL13 — Keep format and quality policy centralized.** Do not scatter bitrate
  literals or ffmpeg format decisions across UI code.
- **DL14 — Test options as behavior.** Tests must pin safety-critical options
  such as playlist disabling and expected postprocessors.

## 8. Enrichment and tagging pipeline

- **E1 — Separate research from tagging.** Provider lookup returns `TrackTags`;
  `apply_tags` owns media mutation and renaming.
- **E2 — Keep one normalized schema.** Provider-specific response shapes must be
  validated and converted before leaving `Enricher`.
- **E3 — Reject malformed model output.** Missing JSON, invalid JSON, or an
  incompatible schema must become a bounded enrichment error.
- **E4 — Keep original source data verbatim in the prompt.** Do not silently
  rewrite the uploader's title, description, tags, URL, or duration.
- **E5 — Bound prompt inputs.** Large descriptions or future source fields must
  be truncated to a documented safe size.
- **E6 — Research attribution before beautifying tags.** Correct artist identity
  takes precedence over cosmetically complete metadata.
- **E7 — Preserve upload classification.** Maintain the distinction between an
  original, a reupload, and a derivative work.
- **E8 — Preserve derivative provenance.** When known, `based_on` identifies the
  artist and track behind a remix, edit, cover, mashup, or related derivative.
- **E9 — Preserve confidence.** Every successful response carries a high,
  medium, or low artist-attribution confidence.
- **E10 — Treat user hints as evidence.** A hint guides a new lookup; it does not
  bypass provider verification or directly overwrite tags.
- **E11 — Keep backend selection explicit.** `/enrich` changes the active backend
  or enabled state without changing stored credentials.
- **E12 — Keep entered-key replacement atomic.** A valid new key replaces the
  prior in-memory key and resets cached clients for the old provider.
- **E13 — Keep key clearing predictable.** Clearing the entered key recomputes
  the default backend from environment and installed CLIs.
- **E14 — Cache clients only per active key.** Replacing or clearing a key must
  invalidate provider client instances.
- **E15 — Bound server-tool continuations.** Provider pause/continue loops need a
  fixed maximum and a clear exhausted-path error.
- **E16 — Do not retry blindly.** Automatic retries must be limited to
  well-defined transient conditions and must not multiply billed requests
  silently.
- **E17 — Preserve existing ID3 frames.** Update fields owned by Transmute
  without dropping embedded artwork or unrelated valid tags.
- **E18 — Sanitize generated filenames.** Remove path separators, NUL, and
  platform-invalid filename characters before renaming.
- **E19 — Keep file extensions accurate.** A tagged MP3 remains an `.mp3`; do
  not relabel media without conversion.
- **E20 — Avoid silent collision handling.** If the normalized target exists,
  retain the current file or apply an explicit, tested collision policy.
- **E21 — Add provider seams deliberately.** If another backend makes
  `Enricher` branching materially larger, extract an `EnrichmentProvider`
  protocol and one transport module per provider before adding more branches.
- **E22 — Keep provider SDK failures non-fatal to downloads.** Enrichment failure
  must not turn a successfully downloaded MP3 into a failed download job.

## 9. Interface and command behavior

- **U1 — Keep layout declarative.** `build_layout` assembles controls and binds
  render callables; it does not perform workflow operations.
- **U2 — Keep bindings narrow.** Each key binding translates one user gesture
  into an `App` operation.
- **U3 — Keep commands discoverable.** A new slash command must appear in
  `/help` and in the README command table.
- **U4 — Keep command errors actionable.** Invalid values must say what values or
  next action are accepted.
- **U5 — Keep `/key` special.** Secret input uses the masked terminal prompt,
  never the normal command buffer.
- **U6 — Keep `/out` safe.** Resolve `~`, handle bare names consistently, create
  the directory, and report filesystem failures.
- **U7 — Keep `/quality` constrained.** Accept only values defined by
  `QUALITIES`.
- **U8 — Keep `/login` interactive.** Run provider login in the normal terminal
  without freezing or corrupting the full-screen UI.
- **U9 — Keep `/logout` implemented but untested live.** Unit tests may stub the
  subprocess; no validation may remove a real credential.
- **U10 — Keep selection actionable.** Up/down navigation moves only among
  failed and low-confidence history entries.
- **U11 — Keep escape predictable.** Escape closes a modal or clears selection
  and restores normal input focus.
- **U12 — Keep Ctrl-C predictable.** It clears active input or selection first
  and requires a deliberate second press to exit.
- **U13 — Keep Ctrl-D an explicit exit.** It exits the interface without
  changing authentication or deleting output.
- **U14 — Keep status truthful.** Queued, active, output-directory, and bitrate
  indicators must reflect current state.
- **U15 — Keep history bounded visually.** Rendering may summarize older entries
  but must not corrupt the underlying session history.
- **U16 — Keep secrets out of display state.** No modal, notice, toolbar,
  history entry, or formatted fragment may retain a credential.
- **U17 — Test interaction as state.** Prefer direct `App` state-machine tests to
  brittle terminal screenshots for key, modal, retry, and hint behavior.

## 10. Designing future features without debt

- **F1 — Start with the current seam.** Extend the module already responsible
  for the behavior before creating a new layer.
- **F2 — Extract only repeated policy.** Do not generalize code merely because a
  future feature might resemble it.
- **F3 — Use provider adapters for real provider variation.** Authentication,
  search, response parsing, or retry policy are valid reasons for a provider
  boundary.
- **F4 — Use named stages for real pipeline variation.** A step belongs in the
  pipeline when it has a typed input, typed output, and independent failure
  policy.
- **F5 — Keep stages ordered explicitly.** Download, research, tag, and rename
  should not become implicit side effects spread across callbacks.
- **F6 — Design playlist support as fan-out.** A playlist produces bounded child
  jobs; it is not a special case that mutates one `Job` repeatedly.
- **F7 — Design cancellation as state.** Cancellation needs terminal status,
  cleanup ownership, and tests; killing threads is not a design.
- **F8 — Design persistence as versioned data.** Persist stable records, not
  Python implementation objects.
- **F9 — Write persistence atomically.** Use a temporary file and atomic replace
  so interruption cannot corrupt the only state copy.
- **F10 — Do not pickle runtime state.** Never serialize `App`, prompt-toolkit
  objects, locks, executors, SDK clients, or live futures.
- **F11 — Design format support end-to-end.** A new audio format affects yt-dlp,
  ffmpeg, tagging, filenames, settings, help, README, and tests.
- **F12 — Design configuration with precedence.** Defaults, environment, runtime
  commands, and future config files need one documented resolution order.
- **F13 — Keep migrations explicit.** A persisted schema change requires version
  detection, a forward migration, and tests using old data.
- **F14 — Avoid hidden background services.** A database, daemon, web server, or
  watcher requires an explicit operational need and lifecycle.
- **F15 — Avoid a plugin ecosystem before contracts stabilize.** Internal
  protocols may evolve; public third-party APIs require compatibility policy and
  versioning.
- **F16 — Prefer upstream capability.** Before owning extraction, tagging, or TUI
  infrastructure, check whether yt-dlp, mutagen, ffmpeg, or prompt-toolkit
  already exposes the required supported behavior.

## 11. Python implementation standards

- **PY1 — Preserve Python 3.10 compatibility.** Do not use syntax or standard
  library APIs introduced after 3.10 without changing the declared requirement.
- **PY2 — Use `from __future__ import annotations`.** Keep it in modules that use
  modern annotations while supporting Python 3.10.
- **PY3 — Type public boundaries.** Functions crossing module or provider
  boundaries need meaningful parameter and return annotations.
- **PY4 — Prefer dataclasses for domain records.** Use them for structured,
  mutable job or tag data rather than parallel dictionaries.
- **PY5 — Prefer `Path` for filesystem paths.** Convert to strings only at
  third-party interfaces that require them.
- **PY6 — Prefer explicit state names.** Job and selection states must be named
  consistently across implementation, tests, and UI.
- **PY7 — Keep functions focused.** Split parsing, I/O, normalization, and
  presentation when they have distinct failure or testing boundaries.
- **PY8 — Avoid boolean soups.** Prefer a named mode, enum, or small object when
  several booleans describe mutually exclusive behavior.
- **PY9 — Avoid mutable default arguments.** Construct lists, dictionaries, and
  stateful collaborators per instance or per call.
- **PY10 — Avoid mutable module globals.** Constants are acceptable; live
  session state belongs to an instance.
- **PY11 — Avoid broad exceptions internally.** Catch the narrowest expected
  exception whenever the caller can recover meaningfully.
- **PY12 — Allow broad exceptions only at integration boundaries.** yt-dlp,
  provider SDKs, subprocesses, and cleanup boundaries may normalize arbitrary
  third-party failures.
- **PY13 — Preserve causes when useful.** Internal exceptions should retain
  enough context for debugging without exposing secrets to users.
- **PY14 — Use `subprocess.run` safely.** Pass an argument list, set `check`
  deliberately, bound runtime, and capture output only when needed.
- **PY15 — Never use `shell=True` for provider commands.** No current CLI call
  requires shell interpolation.
- **PY16 — Keep filesystem encoding explicit for generated text.** Use UTF-8 for
  schemas and future persisted text.
- **PY17 — Keep ordering deterministic.** Provider precedence, URL submission,
  help rows, and output summaries must not depend on unordered iteration.
- **PY18 — Keep errors bounded.** Never render an unbounded exception, provider
  response, description, or subprocess stream in the TUI.
- **PY19 — Remove dead code.** Do not leave superseded helpers, unused imports,
  commented-out implementations, or abandoned compatibility branches.
- **PY20 — Prefer clarity to cleverness.** A small explicit conditional is
  better than reflection, metaprogramming, or a registry when variation is not
  yet real.

## 12. Comments, docstrings, and user documentation

- **DOC1 — Comment the reason.** A useful comment explains a constraint,
  invariant, or non-obvious external behavior.
- **DOC2 — Do not narrate code.** Never add comments that merely restate the
  assignment, branch, loop, function name, or next line.
- **DOC3 — Do not write diary comments.** Do not record what an agent changed,
  what used to happen, or the chronology of a patch inside source files.
- **DOC4 — Do not write change-log comments.** Git history and release notes own
  historical explanations.
- **DOC5 — Do not address future agents in source comments.** Put durable
  repository rules in this guide and behavior-specific reasons beside the code.
- **DOC6 — Do not speculate.** Avoid comments about hypothetical future
  providers, platforms, refactors, or compatibility without a current contract.
- **DOC7 — Keep compatibility claims evidenced.** State supported versions,
  platforms, and provider behavior only when configuration or tests support them.
- **DOC8 — Make `TODO` concrete.** It must name the missing behavior, blocker, or
  removal condition.
- **DOC9 — Prefer tracked work over vague `TODO`s.** If work is not required by
  the current patch, use an issue instead of leaving an open-ended note.
- **DOC10 — Remove stale prose in the same patch.** A behavior change must update
  or delete adjacent comments, docstrings, help, and README text.
- **DOC11 — Use module docstrings for roles.** A module docstring should state
  ownership or a non-obvious boundary, not repeat the filename.
- **DOC12 — Use function docstrings for contracts.** Document public behavior,
  callback meaning, side effects, and exceptional semantics where not obvious.
- **DOC13 — Skip boilerplate private docstrings.** Clear names and types are
  better than repetitive “returns X” prose.
- **DOC14 — Keep comments close to the constraint.** Security flags, strange
  environment filtering, and external-tool workarounds should be explained
  where they are enforced.
- **DOC15 — Keep comments falsifiable.** Prefer precise statements such as why a
  flag is required over subjective claims such as “safer” or “better.”
- **DOC16 — Keep README user-focused.** Installation, commands, credential
  precedence, outputs, and visible workflows belong in `README.md`.
- **DOC17 — Keep this guide maintainer-focused.** Architecture, invariants,
  testing, and contribution rules belong in `AGENTS.md`.
- **DOC18 — Keep help synchronized.** Command names, accepted arguments, and
  shortcuts must agree across `/help`, README, and implementation.
- **DOC19 — Avoid decorative prose.** Favor direct, maintainable explanations
  over slogans, filler, or claims that the code is “clean,” “simple,” or
  “production-ready.”
- **DOC20 — Let tests outrank prose.** When behavior and documentation disagree,
  verify behavior, fix the defect, and synchronize the documentation.

## 13. Testing standards

- **T1 — Test observable behavior.** Assert state transitions, returned data,
  files, subprocess arguments, or user messages rather than implementation trivia.
- **T2 — Add a regression test for every bug fix.** The test must fail for the
  prior behavior and pass for the fix.
- **T3 — Put interaction tests in `tests/test_app.py`.** Selection, retry, modal,
  key, history, queue, and notice behavior belongs there.
- **T4 — Put download tests in `tests/test_downloader.py`.** URL parsing, yt-dlp
  options, progress, status, and error normalization belong there.
- **T5 — Put enrichment tests in `tests/test_enrich.py`.** Credentials, provider
  selection, subprocesses, response parsing, confidence, tagging, and renaming
  belong there.
- **T6 — Use the real `App` state machine where practical.** Replace its worker
  pool or external edges rather than mocking every internal method.
- **T7 — Stub every network boundary.** Tests must not contact source sites,
  search providers, SDK APIs, or login services.
- **T8 — Stub every provider subprocess.** Tests must not invoke real Claude or
  Codex metadata research.
- **T9 — Stub login and logout subprocesses.** Assertions may inspect arguments;
  tests must not alter real authentication.
- **T10 — Never download real media in tests.** Fake yt-dlp and ffmpeg behavior
  at the service boundary.
- **T11 — Use `tmp_path` for filesystem tests.** Never write test media, schemas,
  histories, or configuration into real home directories.
- **T12 — Isolate environment variables.** Use `monkeypatch` to set and clear
  credentials and provider-related environment state.
- **T13 — Avoid real time.** Patch clocks or timers when testing double Ctrl-C,
  notices, timeouts, or future retry backoff.
- **T14 — Avoid nondeterministic threads.** Use a recording or synchronous pool
  when the test concerns submission or state rather than actual concurrency.
- **T15 — Test concurrency invariants separately.** When a race is the behavior
  under test, coordinate with events or barriers rather than sleeps.
- **T16 — Test failure paths.** Cover malformed provider output, missing tools,
  timeouts, SDK errors, filesystem failures, and download exceptions.
- **T17 — Assert secret absence.** Credential tests must check messages, command
  history, and relevant subprocess arguments for accidental leakage.
- **T18 — Keep fixtures minimal.** A fixture should configure only the state
  necessary for the behavior under test.
- **T19 — Keep tests readable.** Prefer arrange/act/assert flow and descriptive
  names over loops or abstraction that hide the scenario.
- **T20 — Do not weaken assertions to make a test pass.** Fix the behavior or
  update the expectation only when the intended contract changed.
- **T21 — Run targeted tests after edits.** Test the changed boundary before
  spending time on the full suite.
- **T22 — Run all tests before handoff.** Documentation-only changes may still
  affect agent behavior and should pass the same repository gate.
- **T23 — Run Ruff before handoff.** New suppressions need a narrow, documented
  reason at the line that requires them.
- **T24 — Keep tests offline and repeatable.** A clean machine with dependencies
  installed must be able to run the suite without credentials or external state.

## 14. Dependencies, configuration, and compatibility

- **DEP1 — Justify every dependency.** Add one only when the standard library and
  existing dependencies cannot reasonably satisfy the requirement.
- **DEP2 — Add runtime dependencies to `[project.dependencies]`.** Do not rely on
  an undeclared transitive package.
- **DEP3 — Add test and lint dependencies to the dev group.** Do not ship
  development-only packages to users.
- **DEP4 — Update `uv.lock` with dependency changes.** Manifest and lockfile must
  describe the same environment.
- **DEP5 — Avoid broad upgrades in feature patches.** Upgrade only packages
  required by the task unless the user requests maintenance work.
- **DEP6 — Keep external executables explicit.** ffmpeg, Claude CLI, and Codex
  CLI requirements or fallbacks must remain documented.
- **DEP7 — Handle missing optional CLIs gracefully.** Missing enrichment CLIs
  disable or skip their path; they do not crash startup.
- **DEP8 — Keep settings typed.** Add a setting to `Settings` or a deliberate
  future configuration model rather than attaching arbitrary attributes.
- **DEP9 — Centralize allowed values.** Quality levels, worker limits, and
  similar policy constants need one source of truth.
- **DEP10 — Define precedence before adding configuration.** Runtime commands,
  environment values, persisted config, and defaults must resolve predictably.
- **DEP11 — Avoid machine-specific paths.** Use `Path.home`, explicit user input,
  or documented environment variables.
- **DEP12 — Keep worktree behavior independent.** Commands and tests must run
  from any Conductor workspace without relying on the original root checkout.
- **DEP13 — Resolve persisted settings in one order.** Output directory and
  bitrate resolve as runtime command → `~/.transmute/settings.json` → built-in
  defaults. An unreadable or schema-incompatible settings file falls back to
  defaults with a bounded warning and is never overwritten implicitly.
- **DEP14 — Keep the version single-sourced.** `transmute/__init__.py` owns
  `__version__`; `pyproject.toml` and the Homebrew formula are derived from it.
  Do not reintroduce a second version literal.
- **DEP15 — Keep the release tag and the version in step.** Tags are bare
  versions such as `0.2a`. `release.yml` compares the tag to the built artifact
  as PEP 440 versions and fails the publish when they disagree, so a `0.2a` tag
  matches the normalised `0.2a0` filename but a `0.3a` tag does not.
- **DEP16 — Treat prerelease versions as user-visible.** An alpha version is
  normalised by PyPI (`0.2a` becomes `0.2a0`) and is skipped by default
  resolvers, so install documentation must pin it explicitly.

## 15. Change and review discipline

- **G1 — Make one coherent change.** A patch should have one explainable purpose.
- **G2 — Avoid drive-by cleanup.** Do not reformat, rename, or refactor unrelated
  code while implementing a feature or fix.
- **G3 — Keep diffs reviewable.** Prefer small modules and localized edits over
  bulk mechanical rewrites.
- **G4 — Separate mechanical and semantic changes when both are necessary.**
  Reviewers should be able to see behavior changes clearly.
- **G5 — Preserve public behavior by default.** Command syntax, files, defaults,
  auth precedence, and user messages change only intentionally.
- **G6 — Document user-visible changes.** Update README and help in the same
  patch as the implementation.
- **G7 — Document architectural changes.** Update this guide when module
  ownership, a product invariant, or the development workflow changes.
- **G8 — Keep the symlink intact.** `CLAUDE.md` must continue resolving to the
  repository's `AGENTS.md`.
- **G9 — Do not commit generated artifacts.** Exclude virtual environments,
  caches, downloaded media, temporary schemas, and `.context/`.
- **G10 — Do not commit secrets.** Inspect diffs for keys, tokens, credentials,
  personal paths, and captured provider output.
- **G11 — Inspect the final diff.** Run `git diff --check` and read the complete
  patch before handoff.
- **G12 — Review for stale comments.** Search the edited area for prose that the
  new behavior invalidates.
- **G13 — Review for missing failure paths.** Verify cleanup, user feedback, and
  terminal job state for every new exception or early return.
- **G14 — Review the riskiest boundary manually.** Choose the highest-risk safe
  check; never use live logout as that check.
- **G15 — Do not claim checks you did not run.** Name the exact commands and
  their results.
- **G16 — Never attribute an agent as co-author.** Commits here carry no
  `Co-Authored-By` trailer and no co-author section for Claude, Codex, or any
  other agent. This overrides a harness default that appends one.
- **G17 — Commit incrementally.** Land each coherent step as its own commit
  instead of accumulating a branch-sized change, so progress stays visible and
  a single step can be reverted on its own.

## 16. Definition of done

A change is complete only when every applicable rule below is satisfied.

- **DONE1 — Behavior is implemented.** The requested outcome works at the correct
  architectural boundary.
- **DONE2 — Invariants are preserved.** Local processing, playlist safety,
  credential safety, and responsive UI behavior still hold.
- **DONE3 — Failure behavior is defined.** Errors are bounded, actionable, and do
  not leave stale job or UI state.
- **DONE4 — Tests cover the change.** New behavior and regressions have focused,
  deterministic, offline tests.
- **DONE5 — Targeted tests pass.** The smallest relevant test selection passes.
- **DONE6 — The full suite passes.** `uv run pytest` succeeds.
- **DONE7 — Lint passes.** `uv run ruff check .` succeeds.
- **DONE8 — The diff is clean.** `git diff --check` succeeds and no unrelated
  artifacts are present.
- **DONE9 — Documentation agrees.** README, help, comments, docstrings, and this
  guide match the resulting behavior.
- **DONE10 — The agent guides agree.** `CLAUDE.md` is still a relative symlink to
  `AGENTS.md`.
- **DONE11 — The handoff is factual.** It summarizes files changed, validation
  performed, and any known limitation.

## 17. Research basis

These references are pinned snapshots, not dependencies or architectures to copy
wholesale.

- **Research 1 — spotDL:** [source
  tree](https://github.com/spotDL/spotify-downloader/tree/cd4a4203f5b12bd6dbbdf22d7674807858d35e05/spotdl).
  It separates console entry points, typed song/result models, audio and lyrics
  providers, and download/post-processing. Transmute adopts the same boundary
  clarity without adopting spotDL's scale.
- **Research 2 — yt-dlp:** [plugin and embedding
  documentation](https://github.com/yt-dlp/yt-dlp/blob/fdcc954df4955267ec1627cbeb347b661a110e7c/README.md#plugins).
  It separates extraction from post-processing and exposes supported embedding
  hooks. Transmute configures those public hooks instead of owning extractor
  implementations.
- **Research 3 — beets:** [importer and metadata-source
  code](https://github.com/beetbox/beets/tree/b7952299941543d4507ac7931edb223acd684b3d/beets).
  It uses explicit pipeline stages, normalized metadata objects, provider
  contracts, and lifecycle events. Transmute adopts typed stages and provider
  seams only when concrete variation justifies them.
- **Research 4 — pgcli:** [application
  code](https://github.com/dbcli/pgcli/tree/fab13f08afa85550208f4dd6c2b0b7eaa2adc861/pgcli).
  It separates prompt-toolkit key bindings, application orchestration,
  execution, and background refresh behavior. Transmute follows that division
  so interface modules remain thin and workers communicate through narrow state
  helpers.
