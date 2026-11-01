"""Spec 012 FR-002 and constitution IV: the URL filter in front of the learning backend."""

from __future__ import annotations

import pytest

from pkpipe.domain.urlfilter import UrlRejected, check_url, parse_deny_list
from pkpipe.learning.errors import UrlBlocked
from pkpipe.learning.urlcheck import check_url as gateway_check


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/talk",
        "http://example.com:8080/a?b=c#d",
        "https://sub.example.org/x",
        "http://93.184.216.34/page",
    ],
)
def test_public_http_urls_pass(url):
    assert check_url(url) == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("ftp://example.com/x", "scheme_not_allowed"),
        ("https://user:secret@example.com/x", "credentials_in_url"),
        ("", "invalid_url"),
        ("https://", "invalid_url"),
        ("https://exa mple.com", "invalid_url"),
        ("https://example.com:99999/", "invalid_url"),
        ("https://example.com/" + "a" * 3000, "invalid_url"),
        ("http://localhost:8080/admin", "private_network"),
        ("http://127.0.0.1/", "private_network"),
        ("http://10.1.2.3/", "private_network"),
        ("http://192.168.0.5/", "private_network"),
        ("http://169.254.169.254/latest/meta-data", "private_network"),
        ("http://[::1]/", "private_network"),
        ("http://[::ffff:127.0.0.1]/", "private_network"),
        ("http://printer.local/", "private_network"),
        ("http://nas.internal/", "private_network"),
        ("http://0.0.0.0/", "private_network"),
    ],
)
def test_unsafe_urls_are_rejected_with_a_reason_code(url, reason):
    with pytest.raises(UrlRejected, match=reason):
        check_url(url)


def test_private_networks_can_be_allowed_by_configuration():
    assert check_url("http://127.0.0.1:8765/p", block_private_networks=False)
    with pytest.raises(UrlRejected, match="scheme_not_allowed"):  # the scheme rule is not configurable
        check_url("file:///etc/passwd", block_private_networks=False)


def test_deny_list_blocks_the_host_and_its_subdomains_only():
    deny = parse_deny_list("# comment\nEvil.example\n\nbad.test  # inline\n")
    assert deny == ["bad.test", "evil.example"]
    for url in ("https://evil.example/x", "https://www.evil.example/x", "https://BAD.test/"):
        with pytest.raises(UrlRejected, match="deny_list"):
            check_url(url, deny=deny)
    assert check_url("https://notevil.example/x", deny=deny)  # a suffix of the string is not a sub domain


def test_deny_list_file_format_matches_secure_fetch():
    """The pack's one deny-list file feeds both URL filters: ``*.`` prefixes and URLs must deny here too."""
    deny = parse_deny_list("*.wild.example\nhttps://url.example/path\n")
    assert deny == ["url.example", "wild.example"]
    for url in ("https://a.wild.example/", "https://url.example/x"):
        with pytest.raises(UrlRejected, match="deny_list"):
            check_url(url, deny=deny)


def test_carrier_grade_nat_and_6to4_count_as_private():
    for addr in ("100.64.1.1", "2002:c0a8:0101::1"):
        with pytest.raises(UrlRejected, match="private_network"):
            check_url("https://n.example/", resolver=lambda h, a=addr: [a])


def test_resolver_result_decides_for_names():
    assert check_url("https://a.example/", resolver=lambda h: ["93.184.216.34"])
    with pytest.raises(UrlRejected, match="private_network"):  # DNS pointing at the LAN
        check_url("https://rebind.example/", resolver=lambda h: ["93.184.216.34", "192.168.1.9"])
    assert check_url("https://nxdomain.example/", resolver=lambda h: [])  # unresolvable: the backend fails


def test_gateway_wrapper_raises_the_learning_error_type():
    with pytest.raises(UrlBlocked, match="private_network"):
        gateway_check("http://localhost/x")
