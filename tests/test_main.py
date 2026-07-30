import pytest

from transmute import __version__
from transmute import main as main_module


@pytest.fixture
def launches(monkeypatch):
    """Replace App so entry-point tests never open the full-screen interface."""
    started = []

    class FakeApp:
        def run(self):
            started.append(True)

    monkeypatch.setattr(main_module, "App", FakeApp)
    return started


def test_no_arguments_starts_the_interface(launches):
    main_module.main([])
    assert launches == [True]


def test_version_reports_the_package_version(launches, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"transmute {__version__}"
    assert launches == []  # --version must not take over the terminal


def test_help_exits_without_starting_the_interface(launches, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--help"])

    assert exit_info.value.code == 0
    assert "usage: transmute" in capsys.readouterr().out
    assert launches == []


def test_unknown_flag_is_rejected(launches, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--bogus"])

    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert launches == []
