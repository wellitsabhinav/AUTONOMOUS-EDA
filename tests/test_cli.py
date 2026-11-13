import pytest

from autonomous_eda import __version__
from autonomous_eda.cli import main


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_mcp_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from importlib.metadata import PackageNotFoundError

    from autonomous_eda.mcp import cli as mcp_cli

    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(mcp_cli, "version", missing)
    assert main(["mcp", "serve", "--config", "unused.toml"]) == 2
    assert "autonomous-eda[mcp]" in capsys.readouterr().err
