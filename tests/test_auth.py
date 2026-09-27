"""Unit tests for lib/auth.py."""

import time

from lib.auth import (
    build_session_cookie_header,
    check_credentials,
    create_session_token,
    get_cookie,
    is_authenticated,
    verify_session_token,
)

SECRET = "test-secret"


def test_valid_token_verifies():
    token = create_session_token(SECRET)
    assert verify_session_token(token, SECRET)


def test_wrong_secret_rejected():
    token = create_session_token(SECRET)
    assert not verify_session_token(token, "wrong-secret")


def test_expired_token_rejected():
    token = create_session_token(SECRET, lifetime=-10)
    assert not verify_session_token(token, SECRET)


def test_tampered_expiry_rejected():
    token = create_session_token(SECRET)
    expiry, _, sig = token.partition(".")
    forged = f"{int(expiry) + 999999}.{sig}"
    assert not verify_session_token(forged, SECRET)


def test_malformed_tokens_rejected():
    assert not verify_session_token("", SECRET)
    assert not verify_session_token("garbage", SECRET)
    assert not verify_session_token("notanumber.abcd", SECRET)
    assert not verify_session_token(None, SECRET)


def test_empty_secret_always_rejects():
    token = create_session_token("")
    assert not verify_session_token(token, "")


def test_check_credentials_correct():
    assert check_credentials("me", "hunter2", "me", "hunter2")


def test_check_credentials_wrong_password():
    assert not check_credentials("me", "wrong", "me", "hunter2")


def test_check_credentials_wrong_username():
    assert not check_credentials("notme", "hunter2", "me", "hunter2")


def test_check_credentials_fails_closed_when_unconfigured():
    assert not check_credentials("me", "hunter2", "", "")
    assert not check_credentials("", "", "", "")


def test_get_cookie_parses_header():
    header = "foo=bar; tracksy_session=abc123; other=1"
    assert get_cookie(header, "tracksy_session") == "abc123"
    assert get_cookie(header, "missing") == ""
    assert get_cookie("", "tracksy_session") == ""


def test_is_authenticated_with_valid_cookie():
    token = create_session_token(SECRET)
    headers = {"Cookie": f"tracksy_session={token}"}
    assert is_authenticated(headers, SECRET)


def test_is_authenticated_without_cookie():
    assert not is_authenticated({}, SECRET)


def test_build_cookie_header_marks_secure_by_default():
    header = build_session_cookie_header({}, SECRET)
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "SameSite=Lax" in header
    assert "tracksy_session=" in header


def test_build_cookie_header_skips_secure_on_plain_http():
    header = build_session_cookie_header({"x-forwarded-proto": "http"}, SECRET)
    assert "Secure" not in header


def test_build_cookie_header_clear_expires_immediately():
    header = build_session_cookie_header({}, SECRET, clear=True)
    assert "Max-Age=0" in header
    assert "tracksy_session=;" in header


def test_round_trip_through_cookie_header():
    set_header = build_session_cookie_header({}, SECRET)
    cookie_value = set_header.split(";", 1)[0]
    assert is_authenticated({"Cookie": cookie_value}, SECRET)
