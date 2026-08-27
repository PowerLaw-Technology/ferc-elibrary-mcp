"""MCP Bundle manifest stays in lockstep with the server and package metadata."""

from __future__ import annotations

import json
import struct
import tomllib
from pathlib import Path

from fastmcp import Client

from ferc_elibrary_mcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_manifest_is_valid_uv_bundle() -> None:
    manifest = _manifest()
    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "ferc-elibrary"
    assert manifest["server"]["type"] == "uv"
    assert manifest["server"]["entry_point"] == "src/ferc_elibrary_mcp/server.py"
    assert (ROOT / manifest["server"]["entry_point"]).is_file()
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "uv.lock").is_file()
    assert not (ROOT / "server" / "lib").exists()
    assert not (ROOT / "server" / "venv").exists()


def test_manifest_version_matches_pyproject() -> None:
    assert _manifest()["version"] == _pyproject()["project"]["version"]


def test_manifest_icon_exists() -> None:
    icon = _manifest()["icon"]
    path = ROOT / icon
    assert path.is_file()
    assert icon.endswith(".png")
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 512)


def test_mcp_config_wires_download_dir_and_idle_timeout() -> None:
    config = _manifest()["server"]["mcp_config"]
    assert config["command"] == "uv"
    assert config["args"] == [
        "run",
        "--directory",
        "${__dirname}",
        "ferc-elibrary-mcp",
    ]
    assert config["env"]["FERC_DOWNLOAD_DIR"] == "${user_config.download_dir}"
    assert config["env"]["FERC_MCP_IDLE_TIMEOUT_SECONDS"] == "14400"
    download = _manifest()["user_config"]["download_dir"]
    assert download["type"] == "directory"
    assert download["default"] == "${HOME}/Downloads/ferc-elibrary"


async def test_manifest_tools_match_server() -> None:
    manifest_names = {tool["name"] for tool in _manifest()["tools"]}
    async with Client(mcp) as session:
        tools = await session.list_tools()
    assert manifest_names == {tool.name for tool in tools}


def test_mcpbignore_excludes_dev_paths() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".mcpbignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "tests/" in patterns
    assert ".github/" in patterns
    assert ".venv/" in patterns
    assert "server/lib/" in patterns
    assert "server/venv/" in patterns
