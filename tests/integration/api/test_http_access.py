"""Spec 014 FR-001: access control of the HTTP surface (token, Host, session, CSRF, headers, scopes)."""

from __future__ import annotations

import pytest

from tests.support.app_stack import AGENT_TOKEN, OWNER_TOKEN, api_client


def test_requests_without_a_valid_token_are_refused(stack):
    """FR-001: no token / wrong token -> 401 on the API; the UI redirects to the login page."""
    anon = api_client(stack, None)
    assert anon.get("/api/runs").status_code == 401
    assert anon.get("/api/runs").headers["www-authenticate"] == "Bearer"
    assert api_client(stack, "wrong-token").get("/api/runs").status_code == 401
    page = anon.get("/triage")
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert anon.post("/api/runs", json={"flow": "triage"}).status_code == 401
    assert anon.get("/api/health").json() == {"ok": True}  # liveness only, no data


def test_unknown_host_header_is_refused_dns_rebinding(stack):
    """FR-001: a rebound host name never reaches the app, with or without a token."""
    from starlette.testclient import TestClient

    client = TestClient(
        stack.web, base_url="http://evil.example", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}
    )
    assert client.get("/api/runs").status_code == 421
    assert client.get("/api/health").status_code == 421


def test_agent_token_is_limited_to_the_mcp_endpoint(stack):
    """FR-001/FR-002: the agent token cannot use the UI, the config API or owner endpoints."""
    agent = api_client(stack, AGENT_TOKEN)
    for method, path in (
        ("get", "/api/runs"),
        ("get", "/api/config/files"),
        ("post", "/api/drafts"),
        ("get", "/triage"),
    ):
        assert getattr(agent, method)(path).status_code == 403, path


def test_security_headers_on_every_response(stack):
    resp = api_client(stack).get("/api/runs")
    assert resp.headers["x-frame-options"] == "DENY" and resp.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["referrer-policy"] == "same-origin"  # not no-referrer: Chrome would send Origin: null
    assert api_client(stack, None).get("/login").headers["x-frame-options"] == "DENY"


def test_login_sets_a_strict_http_only_cookie_and_wrong_token_does_not(stack):
    anon = api_client(stack, None)
    bad = anon.post("/login", data={"token": "nope"})
    assert bad.status_code == 401 and "set-cookie" not in bad.headers
    agent = anon.post("/login", data={"token": AGENT_TOKEN})
    assert agent.status_code == 401, "the agent token must not open a UI session"
    ok = anon.post("/login", data={"token": OWNER_TOKEN})
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert anon.get("/triage").status_code == 200


def test_login_attempts_are_throttled(stack):
    anon = api_client(stack, None)
    codes = [anon.post("/login", data={"token": f"guess-{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert anon.post("/login", data={"token": OWNER_TOKEN}).status_code == 429  # even the right one waits


def _session(stack):
    client = api_client(stack, None)
    assert client.post("/login", data={"token": OWNER_TOKEN}).status_code == 303
    csrf = stack.web.state.sessions.csrf(client.cookies["pkpipe_session"])
    return client, csrf


def test_cookie_requests_that_change_state_need_the_csrf_header(stack):
    """FR-001 CSRF: a cookie alone cannot change state; the token is bound to the session."""
    client, csrf = _session(stack)
    assert client.post("/api/drafts").status_code == 403
    assert client.post("/api/drafts", headers={"X-CSRF-Token": "0" * 64}).status_code == 403
    assert client.post("/api/drafts", headers={"X-CSRF-Token": csrf}).status_code == 201
    assert client.get("/api/drafts").status_code == 200  # reads need no token


def test_cross_origin_cookie_requests_are_refused_even_with_a_valid_csrf_token(stack):
    client, csrf = _session(stack)
    base = {"X-CSRF-Token": csrf}
    assert client.post("/api/drafts", headers={**base, "Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/drafts", headers={**base, "Origin": "null"}).status_code == 403
    assert client.post("/api/drafts", headers={**base, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    same = {**base, "Origin": "http://127.0.0.1:8787", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/drafts", headers=same).status_code == 201


def test_session_cookie_does_not_authorize_the_mcp_endpoint(stack):
    """A browser tab (ambient cookie) must not be able to drive the MCP tools."""
    client, csrf = _session(stack)
    resp = client.post("/mcp", headers={"X-CSRF-Token": csrf}, json={})
    assert resp.status_code == 401


def test_logout_ends_the_session(stack):
    client, csrf = _session(stack)
    assert client.post("/logout", headers={"X-CSRF-Token": csrf}).status_code == 303
    assert client.get("/triage").status_code == 303


@pytest.mark.parametrize("path", ["/static/../tokens.json", "/static/%2e%2e/app.db"])
def test_static_files_cannot_escape_their_directory(stack, path):
    resp = api_client(stack, None).get(path)
    assert resp.status_code in (303, 404, 400) and b"owner" not in resp.content


def test_extra_allowed_hosts_cover_ui_and_mcp_and_nothing_else(stack):
    """FR-001 Tailscale: a MagicDNS name added with --allow-host works (UI and MCP), other names do not."""
    import httpx

    from pkpipe.api.app import create_app
    from tests.support.app_stack import RunningServer
    from tests.support.vault_mcp import free_port

    web = create_app(stack.services, stack.tokens, extra_hosts=("box.tail1234.ts.net",))
    auth = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    probe = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    mcp_headers = {**auth, "Accept": "application/json, text/event-stream"}
    with RunningServer(web, free_port()) as srv:
        ok = httpx.get(f"{srv.url}/api/flows", headers={**auth, "Host": "box.tail1234.ts.net"})
        assert ok.status_code == 200
        mcp = httpx.post(f"{srv.url}/mcp", headers={**mcp_headers, "Host": "box.tail1234.ts.net"}, json=probe)
        assert mcp.status_code not in (401, 403, 421), mcp.text
        other = httpx.get(f"{srv.url}/api/flows", headers={**auth, "Host": "other.ts.net"})
        assert other.status_code == 421
        assert (
            httpx.post(
                f"{srv.url}/mcp", headers={**mcp_headers, "Host": "other.ts.net"}, json=probe
            ).status_code
            == 421
        )


def test_websocket_connections_are_refused(stack):
    from starlette.websockets import WebSocketDisconnect

    client = api_client(stack)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/anything"):
        pass
