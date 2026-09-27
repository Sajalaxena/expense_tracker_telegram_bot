"""Unit tests for lib/ratelimit.py."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lib import ratelimit
from lib.ratelimit import check_rate_limit, get_client_id

T0 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.mode = "select"
        self.filters = []
        self.order_col = None
        self.limit_n = None
        self.want_count = False
        self.payload = None

    def select(self, cols, count=None):
        self.want_count = count == "exact"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r[col] == val)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: r[col] >= val)
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: r[col] < val)
        return self

    def order(self, col):
        self.order_col = col
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def insert(self, row):
        self.mode, self.payload = "insert", row
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def execute(self):
        if self.mode == "insert":
            self.rows.append(dict(self.payload, id=len(self.rows) + 1))
            return SimpleNamespace(data=[self.payload], count=None)
        matched = [r for r in self.rows if all(f(r) for f in self.filters)]
        if self.mode == "delete":
            for r in matched:
                self.rows.remove(r)
            return SimpleNamespace(data=matched, count=None)
        if self.order_col:
            matched.sort(key=lambda r: r[self.order_col])
        total = len(matched)
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        return SimpleNamespace(data=matched, count=total if self.want_count else None)


class FakeClient:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == ratelimit.TABLE
        return FakeQuery(self.rows)


class BrokenClient:
    def table(self, name):
        raise RuntimeError("relation api_requests does not exist")


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    ratelimit._memory.clear()
    ratelimit._memory_global.clear()
    monkeypatch.setattr(ratelimit, "CLEANUP_PROBABILITY", 0)


def test_allows_requests_up_to_limit_then_blocks():
    client = FakeClient()
    limit = ratelimit.RULES["chat"][0][0]
    for i in range(limit):
        assert check_rate_limit(client, "chat", "a", T0 + timedelta(seconds=i)).allowed

    blocked = check_rate_limit(client, "chat", "a", T0 + timedelta(seconds=limit))
    assert not blocked.allowed
    assert 1 <= blocked.retry_after <= 60
    assert "wait" in blocked.message


def test_blocked_requests_are_not_recorded():
    client = FakeClient()
    limit = ratelimit.RULES["chat"][0][0]
    for i in range(limit + 5):
        check_rate_limit(client, "chat", "a", T0 + timedelta(seconds=i))
    assert len(client.rows) == limit


def test_other_clients_and_endpoints_are_unaffected():
    client = FakeClient()
    limit = ratelimit.RULES["chat"][0][0]
    for _ in range(limit):
        check_rate_limit(client, "chat", "a", T0)
    assert not check_rate_limit(client, "chat", "a", T0).allowed
    assert check_rate_limit(client, "chat", "b", T0).allowed
    assert check_rate_limit(client, "insights", "a", T0).allowed


def test_allowed_again_after_window_passes():
    client = FakeClient()
    limit = ratelimit.RULES["chat"][0][0]
    for _ in range(limit):
        check_rate_limit(client, "chat", "a", T0)
    assert check_rate_limit(client, "chat", "a", T0 + timedelta(seconds=61)).allowed


def test_retry_after_reflects_oldest_request_in_window():
    client = FakeClient()
    limit = ratelimit.RULES["chat"][0][0]
    for _ in range(limit):
        check_rate_limit(client, "chat", "a", T0)
    decision = check_rate_limit(client, "chat", "a", T0 + timedelta(seconds=45))
    assert decision.retry_after == 15


def test_hourly_window_enforced(monkeypatch):
    monkeypatch.setitem(ratelimit.RULES, "chat", [(100, 60), (3, 3600)])
    client = FakeClient()
    for i in range(3):
        assert check_rate_limit(client, "chat", "a", T0 + timedelta(minutes=i * 5)).allowed
    assert not check_rate_limit(client, "chat", "a", T0 + timedelta(minutes=20)).allowed
    assert check_rate_limit(client, "chat", "a", T0 + timedelta(minutes=61)).allowed


def test_global_daily_cap_applies_across_clients(monkeypatch):
    monkeypatch.setattr(ratelimit, "DAILY_GLOBAL_CAP", 3)
    client = FakeClient()
    for name in ("a", "b", "c"):
        assert check_rate_limit(client, "chat", name, T0).allowed
    decision = check_rate_limit(client, "chat", "d", T0)
    assert not decision.allowed
    assert "daily" in decision.message
    assert check_rate_limit(client, "chat", "d", T0 + timedelta(days=1, seconds=1)).allowed


def test_falls_back_to_memory_when_db_unavailable():
    limit = ratelimit.RULES["insights"][0][0]
    for i in range(limit):
        assert check_rate_limit(BrokenClient(), "insights", "a", T0 + timedelta(seconds=i)).allowed
    assert not check_rate_limit(BrokenClient(), "insights", "a", T0 + timedelta(seconds=limit)).allowed
    assert check_rate_limit(BrokenClient(), "insights", "b", T0).allowed
    assert check_rate_limit(BrokenClient(), "insights", "a", T0 + timedelta(seconds=61)).allowed


def test_memory_fallback_daily_cap(monkeypatch):
    monkeypatch.setattr(ratelimit, "DAILY_GLOBAL_CAP", 2)
    assert check_rate_limit(BrokenClient(), "chat", "a", T0).allowed
    assert check_rate_limit(BrokenClient(), "chat", "b", T0).allowed
    assert not check_rate_limit(BrokenClient(), "chat", "c", T0).allowed


def test_get_client_id_prefers_real_ip_and_hashes():
    cid = get_client_id({"x-real-ip": "1.2.3.4", "x-forwarded-for": "9.9.9.9"})
    assert cid == get_client_id({"x-real-ip": "1.2.3.4"})
    assert "1.2.3.4" not in cid
    assert get_client_id({"x-forwarded-for": "5.6.7.8, 10.0.0.1"}) == get_client_id({"x-real-ip": "5.6.7.8"})
    assert get_client_id({}) == get_client_id({"x-real-ip": "unknown"})
