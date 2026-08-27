"""Download directory resolution, including unexpanded MCPB placeholders."""

from __future__ import annotations

from pathlib import Path

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.config import resolve_download_dir


def test_resolve_download_dir_expands_mcpb_home_placeholder() -> None:
    resolved = resolve_download_dir("${HOME}/Downloads/ferc-elibrary")
    assert resolved == Path.home() / "Downloads" / "ferc-elibrary"
    assert "${HOME}" not in str(resolved)
    assert resolved.is_absolute()


def test_resolve_download_dir_expands_tilde() -> None:
    resolved = resolve_download_dir("~/Downloads/ferc-elibrary")
    assert resolved == Path.home() / "Downloads" / "ferc-elibrary"


def test_resolve_download_dir_expands_downloads_placeholder() -> None:
    resolved = resolve_download_dir("${DOWNLOADS}/ferc-elibrary")
    assert resolved == Path.home() / "Downloads" / "ferc-elibrary"


def test_resolve_download_dir_blank_uses_default() -> None:
    assert resolve_download_dir("") == Path.home() / "Downloads" / "ferc-elibrary"
    assert resolve_download_dir("   ") == Path.home() / "Downloads" / "ferc-elibrary"


def test_unexpanded_home_is_not_relative_to_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    """Claude Desktop has passed `${HOME}/...` literally; that must not mkdir
    under the extension install directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FERC_DOWNLOAD_DIR", "${HOME}/Downloads/ferc-elibrary")
    client = ELibraryClient(rate_limit_seconds=0)
    assert client._download_dir == Path.home() / "Downloads" / "ferc-elibrary"
    assert tmp_path not in client._download_dir.parents
    assert "${HOME}" not in str(client._download_dir)


def test_env_download_dir_is_read_at_client_init(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FERC_DOWNLOAD_DIR", str(tmp_path / "filings"))
    client = ELibraryClient(rate_limit_seconds=0)
    assert client._download_dir == tmp_path / "filings"
