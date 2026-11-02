import pytest
import requests

from stockwatch import krx


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"ok": 1}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._body


class FakeSession:
    """호출 순서대로 응답(또는 예외)을 돌려준다."""
    def __init__(self, *plan):
        self.plan, self.calls = list(plan), 0
        self.headers = {}

    def post(self, *a, **k):
        self.calls += 1
        x = self.plan.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


@pytest.fixture
def waits(monkeypatch):
    got = []
    monkeypatch.setattr(krx.time, "sleep", got.append)
    return got


def client(*plan):
    c = krx.KrxClient()
    c.s = FakeSession(*plan)
    return c


def test_recovers_after_server_errors_with_backoff(waits):
    c = client(Resp(500), Resp(502), Resp(200, {"v": 7}))
    assert c.post("x") == {"v": 7}
    assert c.s.calls == 3 and waits == [1, 3]


def test_gives_up_after_two_retries_and_says_so(waits):
    c = client(Resp(500), Resp(500), Resp(500))
    with pytest.raises(RuntimeError, match="재시도 2회 후 실패"):
        c.post("x")
    assert c.s.calls == 3 and waits == [1, 3]


def test_client_errors_are_not_retried(waits):
    c = client(Resp(403))
    with pytest.raises(RuntimeError) as e:
        c.post("x")
    assert c.s.calls == 1 and waits == [] and "재시도" not in str(e.value)


def test_connection_error_is_retried(waits):
    c = client(requests.ConnectionError("reset"), Resp(200, {"v": 1}))
    assert c.post("x") == {"v": 1} and waits == [1]


def test_timeout_is_retried_only_once(waits):
    c = client(requests.ReadTimeout("slow"), requests.ReadTimeout("slow"), Resp(200))
    with pytest.raises(RuntimeError, match="재시도 1회 후 실패"):
        c.post("x")
    assert c.s.calls == 2 and waits == [1]


def test_breaker_stops_hammering_after_consecutive_failures(waits):
    c = client(*[Resp(500)] * 9, Resp(200))
    for _ in range(krx.BREAKER_AFTER):
        with pytest.raises(RuntimeError):
            c.post("x")
    calls = c.s.calls
    with pytest.raises(RuntimeError, match="연속 3회 실패"):
        c.post("x")
    assert c.s.calls == calls                     # 네트워크를 건드리지 않았다


def test_success_resets_failure_count(waits):
    c = client(Resp(500), Resp(500), Resp(500), Resp(200))
    with pytest.raises(RuntimeError):
        c.post("x")
    assert c._fails == 1
    assert c.post("x") == {"ok": 1} and c._fails == 0
