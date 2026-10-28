# Releasing Transmute

Two distribution channels share one tagged release: PyPI (for `uv tool install`
and `pipx`) and a Homebrew tap (for `brew install`). The tag drives PyPI
automatically; the Homebrew formula is regenerated and committed here after the
tag is pushed.

## One-time setup

These steps need account access and cannot be done from this repository.

1. **Configure PyPI Trusted Publishing.** In the `transmute-cli` project's
   [Publishing settings](https://pypi.org/manage/account/publishing/), verify
   that the publisher matches this GitHub repository and workflow. This lets
   release tags publish without a stored API token:

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

## Versioning and release

Use numeric versions only. Increment the final patch number, such as `0.2.6` to
`0.2.7`; do not add `a`, `b`, `rc`, or other suffixes. Change the version only
when the user requests a release. `transmute/__init__.py` is the single source
for the package version; `pyproject.toml` reads it.

1. Set the requested version in `transmute/__init__.py` and update the README
   install and version examples. Run the full checks, commit, and push `main`.

2. Create and push a bare numeric tag matching the version:

   ```sh
   git tag -a 0.2.7 -m "Release 0.2.7"
   git push origin 0.2.7
   ```

   `release.yml` compares the tag with the built artifact and fails on a
   mismatch. It runs checks on Python 3.10 through 3.13, publishes to PyPI, and
   creates the GitHub release.

3. CI reads the published sdist URL and SHA256 from PyPI, regenerates
   `Formula/transmute.rb` from the tagged lockfile, and commits the formula to
   `main`. The formula update is serialized so concurrent releases cannot race.
   No manual formula commit is needed when this job succeeds.

If the formula-update job fails, regenerate and push the formula manually after
confirming the release is available on PyPI:

   ```sh
   just formula
   git add Formula/transmute.rb
   git commit -m "Update Homebrew formula for 0.2.7"
   git push origin main
   ```

4. Verify against the tap:

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
