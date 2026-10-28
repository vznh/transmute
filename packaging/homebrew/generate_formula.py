"""Generate the Homebrew formula for Transmute from uv.lock.

Homebrew installs a Python application into a private virtualenv and expects one
`resource` block per transitive runtime dependency, each pinned to an sdist URL
and sha256. uv.lock already records exactly that set, so the formula is derived
from the lockfile instead of being maintained by hand and drifting from it.

Usage:

    uv run python packaging/homebrew/generate_formula.py            # to stdout
    uv run python packaging/homebrew/generate_formula.py --write    # to transmute.rb
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no tomllib.
    import tomli as tomllib  # type: ignore[no-redef]

from packaging.markers import Marker
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "uv.lock"
# Homebrew only discovers formulae in a tap's root, `Formula/`, or
# `HomebrewFormula/`. This repository is its own tap, so the generated formula
# has to live in `Formula/` rather than beside the generator.
FORMULA_PATH = REPO_ROOT / "Formula" / "transmute.rb"
INIT_PATH = REPO_ROOT / "transmute" / "__init__.py"

ROOT_PACKAGE = "transmute-cli"

# Kept in step with `license` in pyproject.toml; `brew audit` compares the
# formula's SPDX expression against the upstream package metadata.
LICENSE_EXPRESSION = "GPL-3.0-or-later"

# The formula pins this interpreter, so markers are resolved against it rather
# than against whatever Python happens to be running this script.
PYTHON_VERSION = "3.13"

# Only the architecture varies across supported Homebrew installs; a dependency
# needed on either arch is included so one formula serves both.
MARKER_ENVIRONMENTS = [
    {
        "python_version": PYTHON_VERSION,
        "python_full_version": f"{PYTHON_VERSION}.0",
        "sys_platform": "darwin",
        "platform_system": "Darwin",
        "platform_machine": machine,
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "extra": "",
    }
    for machine in ("arm64", "x86_64")
]


class GeneratorError(RuntimeError):
    pass


def read_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', INIT_PATH.read_text(encoding="utf-8"), re.M)
    if not match:
        raise GeneratorError(f"no __version__ assignment found in {INIT_PATH}")
    return match.group(1)


def load_packages() -> dict[str, list[dict[str, Any]]]:
    lock = tomllib.loads(LOCK_PATH.read_text(encoding="utf-8"))
    packages: dict[str, list[dict[str, Any]]] = {}
    for package in lock["package"]:
        packages.setdefault(package["name"], []).append(package)
    return packages


def marker_applies(marker: str | None) -> bool:
    if not marker:
        return True
    parsed = Marker(marker)
    return any(parsed.evaluate(environment) for environment in MARKER_ENVIRONMENTS)


def _package_for(packages: dict[str, list[dict[str, Any]]], name: str) -> dict[str, Any]:
    for package in packages.get(name, []):
        markers = package.get("resolution-markers")
        if not markers or any(marker_applies(marker) for marker in markers):
            return package
    raise GeneratorError(f"{name} has no lockfile variant for Python {PYTHON_VERSION}")


def runtime_closure(packages: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Transitively resolve the root package's runtime dependencies.

    Dev dependencies live under a separate lockfile table and are excluded, as
    are dependencies whose environment markers exclude the pinned interpreter.
    """
    try:
        root = _package_for(packages, ROOT_PACKAGE)
    except GeneratorError:
        raise GeneratorError(f"{ROOT_PACKAGE} is missing from {LOCK_PATH}") from None

    seen: set[str] = set()
    queue = [dep["name"] for dep in root.get("dependencies", [])]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            package = _package_for(packages, name)
        except GeneratorError:
            raise GeneratorError(f"{name} is referenced but not locked") from None
        for dep in package.get("dependencies", []):
            if marker_applies(dep.get("marker")):
                queue.append(dep["name"])

    return [_package_for(packages, name) for name in sorted(seen)]


def sdist_of(package: dict[str, Any]) -> tuple[str, str]:
    sdist = package.get("sdist")
    if not sdist or "url" not in sdist:
        raise GeneratorError(
            f"{package['name']} {package.get('version', '?')} has no sdist in the lockfile; "
            "Homebrew resources cannot be pinned to a wheel"
        )
    return sdist["url"], sdist["hash"].removeprefix("sha256:")


def needs_compiler(package: dict[str, Any]) -> bool:
    """True when a package ships only platform wheels, meaning pip must build it."""
    wheels = package.get("wheels", [])
    return bool(wheels) and not any("-none-any.whl" in wheel["url"] for wheel in wheels)


def fetch_sha256(url: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            for chunk in iter(lambda: response.read(1 << 16), b""):
                digest.update(chunk)
    except (urllib.error.URLError, TimeoutError):
        return None
    return digest.hexdigest()


def github_tarball_url(tag: str) -> str:
    """The source tarball GitHub generates for a release tag.

    This lets the tap ship before the PyPI project exists, since a tag is all
    Homebrew needs to fetch and verify a source archive. The tag is used
    verbatim because this repository tags bare versions, such as `0.2a`.
    """
    return f"https://github.com/vznh/transmute/archive/refs/tags/{tag}.tar.gz"


def github_tarball(tag: str) -> tuple[str, str] | None:
    url = github_tarball_url(tag)
    sha256 = fetch_sha256(url)
    return None if sha256 is None else (url, sha256)


def fetch_pypi_sdist(version: str) -> tuple[str, str] | None:
    # PyPI addresses releases by their normalised version, so "0.2a" is "0.2a0".
    url = f"https://pypi.org/pypi/transmute-cli/{Version(version)}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    for entry in payload.get("urls", []):
        if entry.get("packagetype") == "sdist":
            return entry["url"], entry["digests"]["sha256"]
    return None


def render_resources(packages: list[dict[str, Any]]) -> str:
    blocks = []
    for package in packages:
        url, sha256 = sdist_of(package)
        blocks.append(
            f'  resource "{package["name"]}" do\n'
            f'    url "{url}"\n'
            f'    sha256 "{sha256}"\n'
            f"  end\n"
        )
    return "\n".join(blocks)


def native_build_dependencies(packages: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return Homebrew build tools and native libraries required by sdists."""
    names = {package["name"] for package in packages if needs_compiler(package)}
    tools: list[str] = []
    libraries: list[str] = []
    if names & {"jiter", "pydantic-core", "cryptography", "qh3"}:
        tools.append("rust")
    if "qh3" in names:
        tools.append("cmake")
    if "cryptography" in names:
        tools.append("pkgconf")
        libraries.append("openssl@3")
    if "pillow" in names:
        libraries.extend(("jpeg-turbo", "zlib"))
    if "cffi" in names:
        libraries.append("libffi")
    return tools, libraries


def render_formula(version: str, url: str, sha256: str, packages: list[dict[str, Any]]) -> str:
    build_tools, native_libraries = native_build_dependencies(packages)
    build_deps = ""
    if build_tools or native_libraries:
        rows = [
            "  # Native Python resources are built from pinned sdists; these tools and",
            "  # libraries satisfy their documented Rust, CMake, OpenSSL, image, and FFI builds.",
        ]
        rows.extend(f'  depends_on "{name}" => :build' for name in build_tools)
        rows.extend(f'  depends_on "{name}"' for name in native_libraries)
        build_deps = "\n".join(rows) + "\n\n"

    openssl_env = (
        '    ENV["OPENSSL_DIR"] = Formula["openssl@3"].opt_prefix.to_s\n'
        if "openssl@3" in native_libraries
        else ""
    )

    install_resources = "    virtualenv_install_with_resources\n"
    if any(package["name"] == "qh3" for package in packages):
        install_resources = f'''    venv = virtualenv_create(libexec, "python{PYTHON_VERSION}")
    resources.each do |resource|
      if resource.name == "qh3"
        # AWS-LC's jitterentropy rejects optimization; superenv replaces its -O0.
        ENV.O0 {{ venv.pip_install resource }}
      else
        venv.pip_install resource
      end
    end
    venv.pip_install_and_link buildpath
'''

    return f"""class Transmute < Formula
  include Language::Python::Virtualenv

  desc "Convert YouTube or SoundCloud links into rich MP3s"
  homepage "https://github.com/vznh/transmute"
  url "{url}"
  sha256 "{sha256}"
  license "{LICENSE_EXPRESSION}"
  head "https://github.com/vznh/transmute.git", branch: "main"

{build_deps}  # Transmute shells out to ffmpeg for audio extraction and conversion.
  depends_on "ffmpeg"
  depends_on "python@{PYTHON_VERSION}"

{render_resources(packages)}
  def install
{openssl_env}{install_resources}  end

  test do
    # Downloading real media needs the network and a source site, so the test
    # only proves the console script and its virtualenv resolve and run.
    #
    # The literal version is asserted rather than Homebrew's `version`, which is
    # parsed from the url: PyPI normalises "0.2a" to "0.2a0" in the filename
    # while the console script reports __version__ verbatim.
    assert_match "transmute {version}", shell_output("#{{bin}}/transmute --version")
    assert_match "usage: transmute", shell_output("#{{bin}}/transmute --help")
  end
end
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="defaults to transmute.__version__")
    parser.add_argument(
        "--source",
        choices=("pypi", "github"),
        default="pypi",
        help="where the formula downloads Transmute itself from (default: pypi)",
    )
    parser.add_argument("--tag", default=None, help="git tag for --source github; defaults to the version")
    parser.add_argument("--url", default=None, help="source URL; overrides --source")
    parser.add_argument("--sha256", default=None, help="source sha256; overrides --source")
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"write to {FORMULA_PATH.relative_to(REPO_ROOT)}",
    )
    args = parser.parse_args(argv)

    try:
        version = args.version or read_version()
        packages = runtime_closure(load_packages())

        if args.url and args.sha256:
            url, sha256 = args.url, args.sha256
        else:
            if args.source == "github":
                tag = args.tag or version
                released = github_tarball(tag)
                missing = f"no GitHub release tarball for tag {tag}"
            else:
                released = fetch_pypi_sdist(version)
                missing = f"transmute-cli {version} is not on PyPI yet"
            if released is None:
                print(
                    f"warning: {missing}; the url and sha256 fields are placeholders",
                    file=sys.stderr,
                )
                url = f"https://pypi.org/project/transmute-cli/{version}/#PENDING-RELEASE"
                sha256 = "0" * 64
            else:
                url, sha256 = released

        formula = render_formula(version, url, sha256, packages)
    except GeneratorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.write:
        FORMULA_PATH.write_text(formula, encoding="utf-8")
        print(f"wrote {FORMULA_PATH} ({len(packages)} resources)", file=sys.stderr)
    else:
        sys.stdout.write(formula)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
