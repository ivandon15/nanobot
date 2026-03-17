"""Tests for SSRF protection in web tools."""

import pytest

from nanobot.agent.tools.web import _validate_url


def test_validate_url_blocks_localhost() -> None:
    ok, err = _validate_url("http://localhost/admin")
    assert not ok
    assert "private" in err.lower() or "internal" in err.lower() or "blocked" in err.lower()


def test_validate_url_blocks_loopback_ip() -> None:
    ok, err = _validate_url("http://127.0.0.1/secret")
    assert not ok


def test_validate_url_blocks_private_class_a() -> None:
    ok, err = _validate_url("http://10.0.0.1/internal")
    assert not ok


def test_validate_url_blocks_private_class_c() -> None:
    ok, err = _validate_url("http://192.168.1.1/router")
    assert not ok


def test_validate_url_blocks_cloud_metadata() -> None:
    ok, err = _validate_url("http://169.254.169.254/latest/meta-data/")
    assert not ok


def test_validate_url_allows_public_url() -> None:
    ok, err = _validate_url("https://example.com/page")
    assert ok, f"Expected ok but got error: {err}"


def test_validate_url_rejects_non_http_scheme() -> None:
    ok, err = _validate_url("ftp://example.com/file")
    assert not ok
    assert "http" in err.lower()


def test_validate_url_rejects_missing_domain() -> None:
    ok, err = _validate_url("http://")
    assert not ok
