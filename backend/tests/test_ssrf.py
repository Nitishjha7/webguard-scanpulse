"""SSRF guard.

Pure unit tests — no Flask, no database. These addresses are the ones an
attacker reaches for when they get to choose a URL our workers will fetch.
"""
import ipaddress
from unittest.mock import patch

import pytest

from app.utils import ssrf
from app.utils.ssrf import SSRFError


def _fake_getaddrinfo(*addresses: str):
    """Stand in for DNS so tests never depend on a resolver."""

    def _resolver(host, port, *args, **kwargs):
        infos = []
        for addr in addresses:
            family = 10 if ":" in addr else 2
            infos.append((family, 1, 6, "", (addr, port)))
        return infos

    return _resolver


class TestBlockedRanges:
    @pytest.mark.parametrize(
        "address,label",
        [
            ("127.0.0.1", "loopback"),
            ("127.13.37.1", "loopback range"),
            ("10.0.0.5", "RFC1918 /8"),
            ("172.16.4.9", "RFC1918 /12"),
            ("192.168.1.1", "RFC1918 /16"),
            ("169.254.169.254", "cloud metadata"),
            ("169.254.1.1", "link-local"),
            ("100.64.0.1", "carrier-grade NAT"),
            ("0.0.0.0", "unspecified"),
            ("224.0.0.1", "multicast"),
            ("240.0.0.1", "reserved"),
            ("::1", "IPv6 loopback"),
            ("fe80::1", "IPv6 link-local"),
            ("fc00::1", "IPv6 unique-local"),
            ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
            ("::ffff:10.0.0.1", "IPv4-mapped RFC1918"),
        ],
    )
    def test_non_public_addresses_are_rejected(self, address, label):
        with patch("socket.getaddrinfo", _fake_getaddrinfo(address)):
            with pytest.raises(SSRFError, match="non-public"):
                ssrf.resolve_safe_addresses("target.example.com", 443)

    def test_public_addresses_are_allowed(self):
        with patch("socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34")):
            assert ssrf.resolve_safe_addresses("example.com", 443) == ["93.184.216.34"]


class TestMixedResolution:
    def test_one_private_address_among_public_ones_rejects_the_whole_host(self):
        """DNS rebinding: a host with a public *and* a private A record is an attack."""
        with patch("socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34", "127.0.0.1")):
            with pytest.raises(SSRFError, match="127.0.0.1"):
                ssrf.resolve_safe_addresses("rebind.example.com", 443)

    def test_all_public_addresses_are_returned(self):
        with patch("socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34", "1.1.1.1")):
            assert len(ssrf.resolve_safe_addresses("example.com", 443)) == 2


class TestUrlValidation:
    @pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "gopher://x"])
    def test_non_http_schemes_are_rejected(self, url):
        with pytest.raises(SSRFError, match="scheme"):
            ssrf.assert_safe_url(url)

    def test_missing_hostname_is_rejected(self):
        with pytest.raises(SSRFError, match="hostname"):
            ssrf.assert_safe_url("https://")

    def test_default_ports_are_applied(self):
        with patch("socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34")):
            assert ssrf.assert_safe_url("https://example.com")[1] == 443
            assert ssrf.assert_safe_url("http://example.com")[1] == 80

    def test_explicit_port_is_preserved(self):
        with patch("socket.getaddrinfo", _fake_getaddrinfo("93.184.216.34")):
            assert ssrf.assert_safe_url("https://example.com:8443/path")[1] == 8443

    def test_dns_failure_is_reported_as_ssrf_error(self):
        import socket as socket_module

        def _fail(*args, **kwargs):
            raise socket_module.gaierror(-2, "Name or service not known")

        with patch("socket.getaddrinfo", _fail):
            with pytest.raises(SSRFError, match="DNS resolution failed"):
                ssrf.assert_safe_url("https://nope.invalid")


class TestPublicClassification:
    def test_cgnat_is_not_public(self):
        assert ssrf._is_public(ipaddress.ip_address("100.64.0.1")) is False
        # …but the adjacent public space still is.
        assert ssrf._is_public(ipaddress.ip_address("100.128.0.1")) is True
