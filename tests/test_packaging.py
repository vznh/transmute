import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / "packaging" / "homebrew" / "generate_formula.py"


def _load_generator():
    """Load the packaging script by path; it is tooling, not an installed module."""
    spec = importlib.util.spec_from_file_location("generate_formula", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load_generator()


@pytest.fixture(scope="module")
def closure(generator):
    return {package["name"]: package for package in generator.runtime_closure(generator.load_packages())}


def test_closure_contains_every_declared_runtime_dependency(closure):
    assert {"anthropic", "mutagen", "openai", "prompt-toolkit", "yt-dlp"} <= closure.keys()


def test_closure_contains_transitive_dependencies(closure):
    # pydantic-core arrives only through anthropic -> pydantic.
    assert "pydantic-core" in closure
    assert "wcwidth" in closure  # prompt-toolkit


def test_closure_excludes_dev_dependencies(closure):
    assert closure.keys().isdisjoint({"pytest", "ruff", "iniconfig", "pluggy"})


def test_closure_excludes_the_root_package(closure):
    assert "transmute-cli" not in closure


def test_closure_excludes_markers_that_exclude_the_pinned_python(closure):
    # anyio needs exceptiongroup only below 3.11; the formula pins a later one.
    assert "exceptiongroup" not in closure


def test_closure_excludes_windows_only_dependencies(closure):
    assert "colorama" not in closure  # tqdm requires it only on win32


def test_every_resource_has_a_pinned_sdist(generator, closure):
    for package in closure.values():
        url, sha256 = generator.sdist_of(package)
        assert url.startswith("https://")
        assert len(sha256) == 64


def test_binary_only_packages_are_detected(generator, closure):
    compiled = {name for name, package in closure.items() if generator.needs_compiler(package)}
    # These ship Rust extensions, so the formula must request a build toolchain.
    assert compiled == {"jiter", "pydantic-core"}


def test_github_tarball_url_uses_the_v_prefixed_tag(generator):
    # The release workflow tags v<version>; the formula must ask for the same ref.
    assert generator.github_tarball_url("1.2.3").endswith("/refs/tags/v1.2.3.tar.gz")


def test_explicit_source_is_used_verbatim(generator, closure):
    formula = generator.render_formula(
        "1.2.3", "https://example.invalid/src.tar.gz", "a" * 64, list(closure.values())
    )

    assert 'url "https://example.invalid/src.tar.gz"' in formula
    assert f'sha256 "{"a" * 64}"' in formula


def test_rendered_formula_declares_ffmpeg_and_every_resource(generator, closure):
    formula = generator.render_formula("9.9.9", "https://example.invalid/x.tar.gz", "0" * 64, list(closure.values()))

    assert 'depends_on "ffmpeg"' in formula
    assert 'depends_on "rust" => :build' in formula
    assert formula.count("  resource ") == len(closure)
    assert "transmute --version" in formula
