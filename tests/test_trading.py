import numpy as np
import pandas as pd
import pytest

from stockwatch.signals import Result
from stockwatch.trading import engine as eng
from stockwatch.trading.engine import Engine, Gateway
from stockwatch.trading.store import Store  # noqa: F401
from pgtest import new_store
from stockwatch.trading.toss import TossError

CODE = "005930"


class FakeClient:
    def __init__(self):
        self.px, self.bp, self.items = 100_000.0, 10_000_000.0, []
        self.orders, self.ocos, self.cancelled, self.fail = [], [], [], False
        self.order_state, self.cond_state = {}, {}

    def prices(self, syms): return {s: self.px for s in syms}
    def buying_power(self, currency="KRW"): return self.bp
    def holdings(self, symbol=None): return {"items": self.items}
    def open_orders(self, symbol=None): return []

    def create_order(self, **kw):
        if self.fail:
            raise TossError(500, "internal-error", "boom")
        self.orders.append(kw)
        oid = f"O{len(self.orders)}"
        self.order_state[oid] = {"status": "FILLED", "execution": {"filledQuantity": str(kw["quantity"]),
                                 "averageFilledPrice": str(kw["price"]), "commission": "100", "tax": "0"}}
        return {"orderId": oid, "clientOrderId": kw["client_order_id"]}

    def order(self, oid): return self.order_state[oid]

    def create_oco(self, **kw):
        self.ocos.append(kw); cid = f"C{len(self.ocos)}"
        self.cond_state[cid] = {"status": "WATCHING", "first": {}, "second": {}}
        return {"conditionalOrderId": cid}

    def conditional_order(self, cid): return self.cond_state[cid]
    def cancel_conditional(self, cid): self.cancelled.append(cid)


class FakeProvider:
    def __init__(self, df=None, kospi_up=True):
        n = 260; t = np.arange(n)
        close = 100_000 * (1 + 0.002 * t) + 2_500 * np.sin(t / 3)
        idx = pd.bdate_range(end="2026-10-28", periods=n)
        self.df = df if df is not None else pd.DataFrame(
            {"open": close * 0.998, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6}, index=idx)
        self.kospi_up = kospi_up
        self.flow = pd.DataFrame({"institution": [1000] * 7, "foreign": [2000] * 7, "individual": [-3000] * 7},
                                 index=idx[-7:])

    def prices(self, code): return self.df
    def flows(self, code): return self.flow
    def vix(self): return 15.0
    def table(self, folder, code):
        if folder == "index":
            v = np.linspace(100, 200, 100) if self.kospi_up else np.linspace(200, 100, 100)
            return pd.DataFrame({"clsprc_idx": v})


CFG = {"watchlist": [{"code": CODE, "name": "삼성전자", "group": "반도체"}],
       "trading": {"capital_krw": 10_000_000, "daily_buy_limit_krw": 5_000_000, "max_order_krw": 0, "monthly_kill_pct": 5,
                   "buy_max_rsi": 101, "buy_min_score": 65, "max_positions": 3, "regime_filter": True}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x"); monkeypatch.setenv("TOSS_CLIENT_SECRET", "y")
    monkeypatch.setenv("TOSS_EXPIRED", "2027.10.29")
    monkeypatch.setattr(eng, "analyze", lambda *a, **k: Result(CODE, "삼성전자", 100000, 70, 0, "매수 적기"))


def make(env, prov=None, rule=None, live=False):
    store, fake = new_store(), FakeClient()
    e = Engine(store, Gateway(lambda: fake), CFG, prov or FakeProvider())
    store.save_rule(CODE, budget_krw=3_000_000, live=bool(rule and rule.get("live")), **{k: v for k, v in (rule or {}).items() if k != "live"})
    if live:
        store.set("live_enabled", "1")
    return e, store, fake


def first_buy(e):
    scr = e.generate()
    assert scr[CODE]["buy"]["passed"], [c for c in scr[CODE]["buy"]["evidence"] if not c["passed"]]
    return scr[CODE]["buy"]["proposal_id"]


def test_buy_proposal_created_with_quantitative_evidence(env):
    e, store, _ = make(env)
    pid = first_buy(e)
    p = store.proposal(pid)
    assert p["qty"] >= 1 and p["stop"] < p["price"] < p["target"] and p["dry_run"] == 1
    assert all({"label", "passed", "value"} <= set(c) for c in p["evidence"]) and len(p["evidence"]) >= 10


def test_market_regime_blocks_buy(env):
    e, store, _ = make(env, prov=FakeProvider(kospi_up=False))
    scr = e.generate()
    assert not scr[CODE]["buy"]["passed"]
    assert any(c["label"].startswith("시장 국면") and not c["passed"] for c in scr[CODE]["buy"]["evidence"])
    assert store.proposals("pending") == []


def test_no_capital_means_no_buy(env):
    e, store, _ = make(env)
    e.t = {**e.t, "capital_krw": 0}
    assert not e.generate()[CODE]["buy"]["passed"]


def test_dry_run_never_sends_order(env):
    e, store, fake = make(env, rule={"live": True}, live=False)   # 종목 스위치만 ON, 전역 OFF
    r = e.approve(first_buy(e))
    assert r == {"ok": True, "mode": "dry-run"} and fake.orders == []


def test_live_requires_both_switches(env):
    e, store, fake = make(env, rule={"live": False}, live=True)   # 전역만 ON
    assert e.approve(first_buy(e))["mode"] == "dry-run" and fake.orders == []


def test_live_order_once_even_if_double_clicked(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e)
    assert e.approve(pid)["ok"]
    assert not e.approve(pid)["ok"]                                # 두 번째 클릭은 거부
    assert len(fake.orders) == 1 and fake.orders[0]["client_order_id"] == f"sw{pid}"
    assert fake.orders[0]["quantity"] <= 30                        # 한도금액 300만 / 10만원


def test_price_drift_blocks_stale_proposal(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e)
    fake.px = 106_000.0                                            # 6% 급등
    r = e.approve(pid)
    assert r.get("blocked") and fake.orders == [] and store.proposal(pid)["status"] == "blocked"


def test_never_exceeds_buying_power(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e)
    fake.bp = 250_000.0                                            # 현금이 2주분뿐
    e.gw.invalidate()
    assert e.approve(pid)["ok"] and fake.orders[0]["quantity"] == 2


def test_three_failures_engage_kill_switch(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    fake.fail = True
    for _ in range(3):
        store.expire_pending(CODE, "BUY")
        e.approve(first_buy(e))
        store.db.execute("UPDATE proposals SET decided_at=NULL WHERE status='failed'")
    assert e.killed() and store.get("live_enabled") == "0"
    store.db.execute("DELETE FROM proposals")
    assert not e.generate()[CODE]["buy"]["passed"]                 # 킬스위치 중엔 제안도 막힘


def test_monthly_loss_kill_switch(env):
    e, store, fake = make(env)
    store.add_ledger(CODE, "BUY", 10, 100_000, 0)
    store.add_ledger(CODE, "SELL", 10, 90_000, 0)                  # -10만원... 한도는 50만원
    assert not e.check_kill()
    store.add_ledger(CODE, "BUY", 10, 100_000, 0); store.add_ledger(CODE, "SELL", 10, 40_000, 0)  # -60만원
    assert e.check_kill() and "월 손실" in store.get("kill_reason")


def test_unknown_holdings_blocks_buy(env, monkeypatch):
    e, store, fake = make(env)
    monkeypatch.setattr(fake, "holdings", lambda symbol=None: (_ for _ in ()).throw(TossError(500, None, "down")))
    assert not e.generate()[CODE]["buy"]["passed"]


def test_fill_registers_protection_oco_then_stop_hit_is_recorded(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e)
    e.approve(pid)
    notes = e.sync_orders()
    p = store.proposal(pid)
    assert p["status"] == "filled" and p["protect_order_id"] == "C1"
    oco = fake.ocos[0]
    assert oco["stop_trigger"] < p["price"] < oco["target_trigger"] and oco["stop_price"] < oco["stop_trigger"]
    # 서버가 손절을 실행한 상황
    fake.order_state["S1"] = {"status": "FILLED", "execution": {"filledQuantity": str(p["qty"]), "averageFilledPrice": "90000"}}
    fake.cond_state["C1"] = {"status": "COMPLETED", "first": {}, "second": {"triggeredOrderId": "S1"}}
    e.sync_orders()
    assert store.realized_month() < 0 and store.proposal(pid)["protect_order_id"] is None


def test_sell_is_aggressive_and_cancels_protection_first(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e); e.approve(pid); e.sync_orders()
    qty = store.proposal(pid)["qty"]
    fake.items = [{"symbol": CODE, "quantity": str(qty), "averagePurchasePrice": "100000", "lastPrice": "100000"}]
    fake.px = 80_000.0                                             # 급락 → 손절선/20일선 이탈
    scr = e.generate()
    sell = scr[CODE]["sell"]
    assert sell["passed"] and sell["proposal_id"]
    r = e.approve(sell["proposal_id"])
    assert r["ok"] and fake.cancelled == ["C1"]                    # 보호 OCO 해제 후 매도
    assert fake.orders[-1]["side"] == "SELL" and fake.orders[-1]["price"] < 80_000


def test_business_rejection_is_blocked_not_counted_as_system_failure(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    orig = fake.create_order
    fake.create_order = lambda **kw: (_ for _ in ()).throw(TossError(422, "order-hours-closed", "장 운영 시간이 아닙니다"))
    for _ in range(4):                                             # 장 마감 후 반복 승인해도
        store.db.execute("DELETE FROM proposals")
        r = e.approve(first_buy(e))
        assert r.get("blocked")
    assert not e.killed() and store.get("fail_streak", "0") == "0"
    fake.create_order = orig


def test_notifications_on_proposal_and_kill(env):
    sent = []
    e, store, fake = make(env)
    e.notify = lambda t, b: sent.append((t, b))
    first_buy(e)
    assert sent and "매수 제안" in sent[0][0] and "원" not in sent[0][1]      # 금액 정보는 싣지 않는다
    e.engage_kill("테스트")
    assert any("킬스위치" in t for t, _ in sent)


def test_push_send_without_config_is_silent_noop(env, monkeypatch):
    from stockwatch.trading import push
    monkeypatch.setattr(push, "configured", lambda: False)
    store = new_store(); store.add_push_token("t" * 100)
    assert push.send(store, "a", "b")["sent"] == 0


def test_push_send_posts_v1_and_prunes_dead_tokens(env, monkeypatch):
    from stockwatch.trading import push
    calls = []

    class R:
        def __init__(self, code, text=""): self.status_code, self.text = code, text

    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "_auth", lambda: ("TOKEN", "proj"))
    monkeypatch.setattr(push.requests, "post", lambda url, **kw: (calls.append((url, kw)),
                        R(200) if kw["json"]["message"]["token"] == "good" else R(404, "UNREGISTERED"))[1])
    store = new_store(); store.add_push_token("good"); store.add_push_token("dead")
    r = push.send(store, "제목", "본문")
    assert r["sent"] == 1 and r["failed"] == 1 and store.push_token_count() == 1
    assert calls[0][0] == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer TOKEN"


def test_cannot_remove_held_or_ordered_stock(env):
    e, store, fake = make(env, rule={"live": True}, live=True)
    assert e.removal_blockers(CODE) == []
    pid = first_buy(e); e.approve(pid)
    assert any("진행 중" in w for w in e.removal_blockers(CODE))
    e.sync_orders()
    assert any("보호주문" in w for w in e.removal_blockers(CODE))
    fake.items = [{"symbol": CODE, "quantity": "3", "averagePurchasePrice": "1", "lastPrice": "1"}]
    assert any("보유 중" in w for w in e.removal_blockers(CODE))


def test_push_problem_reports_missing_key_file(monkeypatch, tmp_path):
    from stockwatch.trading import push
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT", str(tmp_path / "nope.json"))
    assert "찾을 수 없습니다" in push.problem() and not push.configured()
    (tmp_path / "nope.json").write_text("{}")
    assert push.problem() is None and push.configured()
