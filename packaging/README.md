# Releasing Transmute

Two distribution channels share one tagged release: PyPI (for `uv tool install`
and `pipx`) and a Homebrew tap (for `brew install`). The tag drives PyPI
automatically; the tap is updated by hand because it lives in another
repository.

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

3. **Create the tap repository.** Homebrew resolves
   `brew install vznh/transmute/transmute` to the GitHub repository
   `vznh/homebrew-transmute`, with the formula at `Formula/transmute.rb`. The
   repository must be public.

## Per release

1. Bump `__version__` in `transmute/__init__.py`. It is the only place the
   version is written; `pyproject.toml` reads it.

2. Tag and push. The tag must be the version with a `v` prefix, because
   `release.yml` checks the tag against the built artifact and fails on a
   mismatch:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

   The workflow runs the suite on Python 3.10 through 3.13, publishes to PyPI,
   and attaches the sdist to a GitHub release.

3. Regenerate the formula once the release is on PyPI, so it picks up the real
   sdist URL and sha256:

   ```sh
   just formula
   ```

   Without a matching PyPI release the generator warns and writes placeholder
   `url` and `sha256` fields, which Homebrew will reject.

4. Copy `packaging/homebrew/transmute.rb` to `Formula/transmute.rb` in the tap
   repository and push.

5. Verify against the real tap:

   ```sh
   brew install vznh/transmute/transmute
   brew test vznh/transmute/transmute
   ```

## Notes on the formula

`generate_formula.py` derives one Homebrew resource per transitive runtime
dependency from `uv.lock`, so the formula never disagrees with the lockfile.
Environment markers are resolved against the interpreter the formula pins
(`python@3.13`), which is why Windows-only and Python 3.10-only dependencies do
not appear. `tests/test_packaging.py` covers that closure.

`pydantic-core` and `jiter` publish no pure-Python wheel, so the formula
requests `depends_on "rust" => :build` and pip compiles them from the pinned
sdist. That is the slowest part of `brew install`. Making the `anthropic` and
`openai` SDKs optional extras would remove both, along with most of the other
resources — their imports in `transmute/enrich.py` are already lazy, so only
the credential-selection path would need to handle a missing SDK.

Regenerate the formula whenever a runtime dependency, the lockfile, or the
version changes.
