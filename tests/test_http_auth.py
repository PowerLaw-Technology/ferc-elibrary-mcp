from ferc_elibrary_mcp.http.auth_config import build_auth_provider, parse_auth_tokens


def test_parse_auth_tokens() -> None:
    raw = '{"roselle": {"token": "secret123", "scopes": ["ferc"]}}'
    data = parse_auth_tokens(raw)
    assert data["roselle"]["token"] == "secret123"


def test_build_auth_provider_from_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "FERC_MCP_AUTH_TOKENS",
        '{"acme": {"token": "tok-acme", "scopes": ["ferc"]}}',
    )
    provider = build_auth_provider()
    assert provider is not None
