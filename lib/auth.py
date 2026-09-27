"""Cookie-based session authentication for the Tracksy web UI.

A session is a `{expiry}.{hmac}` token, HMAC-SHA256 signed with SESSION_SECRET,
stored in an HttpOnly cookie. There is no server-side session store — the
signature and expiry embedded in the token are the only state, so verification
is a pure function of the token and the secret.
"""

import hashlib
import hmac
import time

COOKIE_NAME = "tracksy_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 3600  # 30 days


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(secret: str, lifetime: int = SESSION_LIFETIME_SECONDS) -> str:
    expiry = str(int(time.time()) + lifetime)
    return f"{expiry}.{_sign(expiry, secret)}"


def verify_session_token(token: str, secret: str) -> bool:
    if not secret or not token or "." not in token:
        return False
    expiry, _, signature = token.partition(".")
    if not expiry.isdigit():
        return False
    if not hmac.compare_digest(_sign(expiry, secret), signature):
        return False
    return int(expiry) > int(time.time())


def check_credentials(username: str, password: str, expected_username: str, expected_password: str) -> bool:
    """Constant-time credential check. Fails closed if either expected value is unset."""
    if not expected_username or not expected_password:
        return False
    user_ok = hmac.compare_digest((username or "").encode("utf-8"), expected_username.encode("utf-8"))
    pass_ok = hmac.compare_digest((password or "").encode("utf-8"), expected_password.encode("utf-8"))
    return user_ok and pass_ok


def get_cookie(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def is_authenticated(headers, secret: str) -> bool:
    token = get_cookie(headers.get("Cookie", ""), COOKIE_NAME)
    return verify_session_token(token, secret)


def build_session_cookie_header(headers, secret: str, clear: bool = False) -> str:
    """Build a Set-Cookie header value. `headers` is the request's headers,
    used only to decide whether to mark the cookie Secure (skipped for plain
    http, e.g. local `vercel dev`)."""
    proto = headers.get("x-forwarded-proto", "https")
    secure = "; Secure" if proto != "http" else ""
    if clear:
        return f"{COOKIE_NAME}=; Path=/; HttpOnly{secure}; SameSite=Lax; Max-Age=0"
    token = create_session_token(secret)
    return f"{COOKIE_NAME}={token}; Path=/; HttpOnly{secure}; SameSite=Lax; Max-Age={SESSION_LIFETIME_SECONDS}"
