"""Rate limiting for the Gemini-backed API endpoints.

Requests are recorded in the Supabase `api_requests` table (see
supabase_api_requests.sql) so limits hold across serverless instances. If that
table is unavailable the limiter falls back to per-instance memory, which is
weaker but still throttles a single client hitting a warm instance.

Two layers protect the Gemini quota:
  * per-client sliding windows (RULES), keyed by a hash of the client IP
  * a global daily cap across all clients (DAILY_GLOBAL_CAP)
"""

import hashlib
import math
import random
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TABLE = "api_requests"

# endpoint -> [(max requests, window in seconds), ...]
RULES = {
    "chat": [(10, 60), (60, 3600)],
    "insights": [(5, 60), (20, 3600)],
    "login": [(5, 300), (15, 3600)],
    "media": [(10, 60), (60, 3600)],
}
DAILY_GLOBAL_CAP = 300
CLEANUP_PROBABILITY = 0.02

_memory: dict = defaultdict(deque)
_memory_global: deque = deque()
_lock = threading.Lock()


@dataclass
class Decision:
    allowed: bool
    retry_after: int = 0
    message: str = ""


def get_client_id(headers) -> str:
    """Return a hashed client identifier from proxy headers (raw IPs are not stored)."""
    ip = (
        headers.get("x-real-ip")
        or (headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or "unknown"
    )
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def _wait_message(seconds: int) -> str:
    if seconds >= 120:
        wait = f"{math.ceil(seconds / 60)} minutes"
    else:
        wait = f"{seconds} second{'s' if seconds != 1 else ''}"
    return f"You're sending requests too quickly. Please wait {wait} and try again."


def _blocked(retry_after: int) -> Decision:
    retry_after = max(1, int(retry_after))
    return Decision(False, retry_after, _wait_message(retry_after))


def _daily_cap_decision() -> Decision:
    return Decision(
        False, 3600, "The daily AI usage limit has been reached. Please try again tomorrow."
    )


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check_db(client, endpoint: str, client_id: str, now: datetime) -> Decision:
    for limit, window in RULES[endpoint]:
        window_start = (now - timedelta(seconds=window)).isoformat()
        rows = (
            client.table(TABLE)
            .select("created_at")
            .eq("endpoint", endpoint)
            .eq("client_hash", client_id)
            .gte("created_at", window_start)
            .order("created_at")
            .limit(limit)
            .execute()
            .data
            or []
        )
        if len(rows) >= limit:
            frees_at = _parse_ts(rows[0]["created_at"]) + timedelta(seconds=window)
            return _blocked(math.ceil((frees_at - now).total_seconds()))

    day_start = (now - timedelta(days=1)).isoformat()
    total = (
        client.table(TABLE)
        .select("id", count="exact")
        .gte("created_at", day_start)
        .limit(1)
        .execute()
        .count
        or 0
    )
    if total >= DAILY_GLOBAL_CAP:
        return _daily_cap_decision()

    client.table(TABLE).insert(
        {"endpoint": endpoint, "client_hash": client_id, "created_at": now.isoformat()}
    ).execute()

    if random.random() < CLEANUP_PROBABILITY:
        try:
            cutoff = (now - timedelta(days=2)).isoformat()
            client.table(TABLE).delete().lt("created_at", cutoff).execute()
        except Exception:
            pass

    return Decision(True)


def _check_memory(endpoint: str, client_id: str, now: datetime) -> Decision:
    t = now.timestamp()
    rules = RULES[endpoint]
    longest = max(window for _, window in rules)

    with _lock:
        hits = _memory[(endpoint, client_id)]
        while hits and t - hits[0] >= longest:
            hits.popleft()

        for limit, window in rules:
            in_window = [h for h in hits if t - h < window]
            if len(in_window) >= limit:
                return _blocked(math.ceil(in_window[0] + window - t))

        while _memory_global and t - _memory_global[0] >= 86400:
            _memory_global.popleft()
        if len(_memory_global) >= DAILY_GLOBAL_CAP:
            return _daily_cap_decision()

        hits.append(t)
        _memory_global.append(t)

    return Decision(True)


def check_rate_limit(client, endpoint: str, client_id: str, now: datetime | None = None) -> Decision:
    """Record a request and return whether it is allowed.

    `client` is a Supabase client (SupabaseDB.client). Falls back to in-memory
    limiting if the database check fails (e.g. the table has not been created).
    """
    now = now or datetime.now(timezone.utc)
    try:
        return _check_db(client, endpoint, client_id, now)
    except Exception as exc:
        print(f"Rate limiter DB check failed, using in-memory fallback: {exc}")
        return _check_memory(endpoint, client_id, now)
