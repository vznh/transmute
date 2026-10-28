# Releasing Transmute

Two distribution channels share one tagged release: PyPI (for `uv tool install`
and `pipx`) and a Homebrew tap (for `brew install`). The tag drives PyPI
automatically; the Homebrew formula is regenerated and committed here after the
tag is pushed.

## One-time setup

These steps need account access and cannot be done from this repository.

1. **Reserve the PyPI project.** `transmute-cli` is unclaimed. Create a
   [pending publisher](https://pypi.org/manage/account/publishing/) so the first
   tag can publish without an API token:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `transmute-cli` |
   | Owner | `vznh` |
   | Repository name | `transmute` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

2. **Create the `pypi` GitHub environment.** Repository settings →
   Environments → New environment → `pypi`. Restrict it to tag pushes if you
   want a manual approval gate before publishing.

3. **Nothing to create — this repository is the tap.** `Formula/transmute.rb`
   is generated here and served from here, so there is no separate
   `homebrew-`-prefixed repository to maintain.

   Homebrew normally derives a tap's URL from its name, mapping `vznh/transmute`
   to `github.com/vznh/homebrew-transmute`. Passing the URL explicitly overrides
   that, which is the whole reason a second repository is unnecessary:

   ```sh
   brew tap vznh/transmute https://github.com/vznh/transmute
   ```

   Homebrew discovers formulae only in a tap's root, `Formula/`, or
   `HomebrewFormula/`, which is why the generated file lives in `Formula/` and
   not beside its generator. No formula named `transmute` exists in
   homebrew-core, so `brew install transmute` is unambiguous once tapped.

## Per release

1. Bump `__version__` in `transmute/__init__.py`. It is the only place the
   version is written; `pyproject.toml` reads it.

2. Tag and push. Tags are bare versions, for example:

   ```sh
   git tag 0.2.3a
   git push origin 0.2.3a
   ```

   `release.yml` compares the tag to the built artifact as PEP 440 versions and
   fails on a mismatch. That comparison is deliberately not a string match:
   packaging normalises `0.2.3a` to `0.2.3a0`, so the tag and the filename
   `transmute_cli-0.2.3a0.tar.gz` differ in text while naming one release.

   The workflow runs the suite on Python 3.10 through 3.13, publishes to PyPI,
   and attaches the sdist to a GitHub release, marked as a prerelease when the
   version is one.

3. Regenerate the formula once the release is on PyPI, so it picks up the real
   sdist URL and sha256:

   ```sh
   just formula
   ```

   Without a matching PyPI release the generator warns and writes placeholder
   `url` and `sha256` fields, which Homebrew will reject.

   The tap does not have to wait for PyPI. A pushed tag is enough on its own,
   because GitHub serves a source tarball for it:

   ```sh
   uv run python packaging/homebrew/generate_formula.py --source github --write
   ```

   The tag is taken verbatim from `__version__`; pass `--tag` when they differ.
   Both sources build the same package; PyPI is preferred once the project
   exists because its sdist is the artifact the release workflow verified.

4. Commit the regenerated `Formula/transmute.rb` and push it to `main`.
   Homebrew serves a tap from its default branch, so the release is not
   installable until the formula is on `main`.

5. Verify against the real tap:

   ```sh
   brew tap vznh/transmute https://github.com/vznh/transmute
   brew trust vznh/transmute   # Homebrew will not load a formula from an untrusted tap
   brew install transmute
   brew test transmute
   ```

## Notes on the formula

`generate_formula.py` derives one Homebrew resource per transitive runtime
dependency from `uv.lock`, so the formula never disagrees with the lockfile.
Environment markers are resolved against the interpreter the formula pins
(`python@3.13`), which is why Windows-only and Python 3.10-only dependencies do
not appear. `tests/test_packaging.py` covers that closure.

The formula builds Python resources from pinned sdists, including native Rust
and C extensions. iPhone sync's `pymobiledevice3` dependency brings in `qh3`,
which builds AWS-LC. AWS-LC's jitterentropy sources require `-O0`; Homebrew's
superenv otherwise replaces that flag with its default optimization level.
The generator installs `qh3` inside an `ENV.O0` block and restores normal
optimization for the other resources. Preserve this scope when changing the
install block and verify changes with a full Homebrew build, not just Ruby
syntax checks.

Regenerate the formula whenever a runtime dependency, the lockfile, or the
version changes.
