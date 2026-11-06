"""Spec 023 US4: the UI over Tailscale. ``ui.json`` (public_url, allowed_hosts) extends the Host
allowlist; the
owner token, the session CSRF token and the same-origin check stay exactly as they were."""

from __future__ import annotations

import pytest

from pkpipe.api.app import create_app
from pkpipe.api.security import BindRefused, check_bind
from pkpipe.serve import ui_settings
from tests.support.app_stack import OWNER_TOKEN

MAGIC = "mini.tail1234.ts.net"
TS_IP = "100.64.0.10"
UI = {"public_url": f"https://{MAGIC}", "allowed_hosts": [TS_IP]}


@pytest.fixture
def web(stack):
    hosts, public_url = ui_settings({"ui": UI})
    return create_app(
        stack.services, stack.tokens, bind_host="127.0.0.1", extra_hosts=hosts, public_url=public_url
    )


def client(web, host):
    from starlette.testclient import TestClient

    return TestClient(web, base_url=f"http://{host}", follow_redirects=False)


def test_the_hosts_come_from_the_config_and_the_flag():
    hosts, url = ui_settings({"ui": UI}, ("Extra.Example",))
    assert hosts == (TS_IP, MAGIC, "extra.example") and url == f"https://{MAGIC}"
    assert ui_settings({}) == ((), None)
    assert ui_settings({"ui": {"public_url": None, "allowed_hosts": []}}) == ((), None)


def test_check_bind_still_allows_only_loopback_or_a_tailscale_address():
    assert check_bind("127.0.0.1") == "127.0.0.1" and check_bind(TS_IP) == TS_IP
    for bad in ("0.0.0.0", "192.168.1.5", "::", MAGIC):
        with pytest.raises(BindRefused):
            check_bind(bad)


def test_the_magicdns_name_and_the_tailscale_ip_pass_the_host_guard_others_do_not(web):
    for host in (MAGIC, f"{MAGIC}:443", TS_IP, f"{TS_IP}:8787", "127.0.0.1:8787", "localhost"):
        assert client(web, host).get("/api/health").status_code == 200, host
    for host in ("other.ts.net", "100.64.0.11", f"evil.{MAGIC}", "mini.tail1234.ts.net.evil.example"):
        assert client(web, host).get("/api/health").status_code == 421, host


def test_a_token_is_still_required_over_the_magicdns_name(web):
    c = client(web, MAGIC)
    assert c.get("/api/triage").status_code == 401
    assert c.get("/triage").status_code == 303
    assert c.get("/api/triage", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).status_code in (200, 409)
    assert c.get("/api/triage", headers={"Authorization": "Bearer " + "x" * 30}).status_code == 401


def test_login_cookie_is_secure_behind_tailscale_serve_and_csrf_and_origin_still_apply(web):
    c = client(web, MAGIC)
    resp = c.post("/login", data={"token": OWNER_TOKEN}, headers={"Origin": f"http://{MAGIC}"})
    assert resp.status_code == 303 and "secure" in resp.headers["set-cookie"].lower()  # public_url is https
    assert (
        "httponly" in resp.headers["set-cookie"].lower()
        and "samesite=strict" in resp.headers["set-cookie"].lower()
    )
    sid = c.cookies["pkpipe_session"]
    csrf = web.state.sessions.csrf(sid)
    # the Secure cookie is not replayed by the test client over http (tailscale serve terminates TLS)
    base = {"Origin": f"http://{MAGIC}", "Cookie": f"pkpipe_session={sid}"}
    assert c.post("/ui/runs", data={"flow": "triage"}, headers=base).status_code == 403  # no CSRF header
    assert (
        c.post(
            "/ui/runs",
            data={"flow": "triage"},
            headers={**base, "X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    ok = c.post("/ui/runs", data={"flow": "no_such"}, headers={**base, "X-CSRF-Token": csrf})
    assert ok.status_code == 200 and "unknown flow" in ok.text  # same-origin + CSRF: the request is processed


def test_a_login_cannot_be_forged_cross_origin_over_the_magicdns_name(web):
    c = client(web, MAGIC)
    resp = c.post("/login", data={"token": OWNER_TOKEN}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403 and "pkpipe_session" not in c.cookies


def test_without_a_public_url_the_cookie_is_not_marked_secure(stack):
    web = create_app(stack.services, stack.tokens)
    c = client(web, "127.0.0.1:8787")
    resp = c.post("/login", data={"token": OWNER_TOKEN})
    assert resp.status_code == 303 and "secure" not in resp.headers["set-cookie"].lower()
