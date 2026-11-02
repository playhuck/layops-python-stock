from datetime import date

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
    assert scr[CODE]["buy"]["passed"], [c for c in scr[CODE]["buy"]["evidence"] if eng.is_check(c) and not c["passed"]]
    return scr[CODE]["buy"]["proposal_id"]


def test_buy_proposal_created_with_quantitative_evidence(env):
    e, store, _ = make(env)
    pid = first_buy(e)
    p = store.proposal(pid)
    assert p["qty"] >= 1 and p["stop"] < p["price"] < p["target"] and p["dry_run"] == 1
    checks = [c for c in p["evidence"] if c.get("kind") != "info"]               # 참고 정보(kind=info)는 통과 여부가 없다
    assert all({"label", "passed", "value"} <= set(c) for c in checks) and len(checks) >= 10


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


def test_vix_evidence_shows_reference_date_when_provider_knows_it(env):
    class P(FakeProvider):
        def vix_date(self): return date(2026, 10, 28)
    e, _, _ = make(env, prov=P())
    ev = {c["label"]: c for c in e.generate()[CODE]["buy"]["evidence"]}
    assert ev["VIX 안정"]["value"] == "15.0 (10/28 마감)" and ev["VIX 안정"]["passed"]
    e2, _, _ = make(env)                                                        # vix_date 가 없는 공급자(CSV/가짜)도 그대로 동작
    assert {c["label"]: c for c in e2.generate()[CODE]["buy"]["evidence"]}["VIX 안정"]["value"] == "15.0"


# -- 매물대 참고 정보(3A): 표시만 하고 판단·개수에는 쓰지 않는다 ---------------------------------------
def test_profile_info_is_neutral_and_never_changes_the_decision(env, monkeypatch):
    e, store, _ = make(env)
    ev = e.generate()[CODE]["buy"]["evidence"]
    info = [c for c in ev if c.get("kind") == "info"]
    assert len(info) == 1 and info[0]["label"] == "위 주요 추정 매물대" and "passed" not in info[0]
    assert "판단 차단 없음" in info[0]["threshold"] and "예측력" in info[0]["threshold"]
    assert info[0]["data"]["method_version"] == 1 and info[0]["data"]["last_date"] == "2026-10-28"
    # 같은 입력에서 정보 항목을 계산 불가/오류로 바꿔도 제안 여부·일반 조건 개수는 같다
    base_checks = [(c["label"], c["passed"]) for c in ev if c.get("kind") != "info"]
    monkeypatch.setattr(eng.volume_profile, "compute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    e2, _, _ = make(env)
    scr = e2.generate()[CODE]["buy"]
    assert scr["passed"] and [(c["label"], c["passed"]) for c in scr["evidence"] if c.get("kind") != "info"] == base_checks
    assert [c for c in scr["evidence"] if c.get("kind") == "info"][0]["value"].startswith("계산 오류")


def test_profile_info_unavailable_reason_and_counts_exclude_info(env):
    short = FakeProvider().df.iloc[-20:]                                         # 거래일 부족 → 계산 불가, 기존 판단은 그대로
    e, store, _ = make(env, prov=FakeProvider(df=short))
    scr = e.generate()[CODE]["buy"]
    info = [c for c in scr["evidence"] if c.get("kind") == "info"][0]
    assert info["value"].startswith("계산 불가") and "거래일 부족" in info["value"]
    assert eng.tally(scr["evidence"]) == (sum(1 for c in scr["evidence"] if c.get("kind") != "info" and c["passed"]), sum(1 for c in scr["evidence"] if c.get("kind") != "info"))


def test_notification_counts_ignore_info_items(env):
    sent = []
    e, store, _ = make(env)
    e.notify = lambda t, b: sent.append((t, b))
    pid = first_buy(e)
    ev = store.proposal(pid)["evidence"]
    ok, total = eng.tally(ev)
    assert total == len(ev) - 1 and ok == total                                   # 조건 충족 제안: 정보 1건을 뺀 모든 조건이 통과
    assert f"근거 {ok}/{total} 충족" in sent[0][1]


def test_old_stored_evidence_without_kind_counts_as_checks():
    old = [{"label": "a", "passed": True, "value": "1", "threshold": ""}, {"label": "b", "passed": False, "value": "2", "threshold": ""}]
    assert eng.tally(old) == (1, 2) and eng.is_check(old[0])


def test_profile_info_failure_while_building_text_does_not_break_the_review(env, monkeypatch):
    real = eng.volume_profile.compute
    monkeypatch.setattr(eng.volume_profile, "compute", lambda *a, **k: {**real(*a, **k), "up": {"lo": "x"}})      # 반환 구조가 어긋나 문장 조립에서 예외
    e, store, _ = make(env)
    scr = e.generate()[CODE]["buy"]
    assert scr["passed"] and scr["proposal_id"]
    assert [c for c in scr["evidence"] if c.get("kind") == "info"][0]["value"].startswith("계산 오류")


# -- 진입 ATR 고정 (원장·토스 보유 검증) --------------------------------------------------------------
def buy_prop(store, atr=2500.0, code=CODE, side="BUY", sizing="atr"):
    return store.add_proposal(code=code, name="x", side=side, qty=10, price=100_000, stop=95_000, target=110_000, evidence=[],
                              sizing={"atr": atr} if sizing == "atr" else sizing, dry_run=False)


def basis(store, qty=10, avg=100_000.0, code=CODE):
    return store.entry_basis(code, qty, avg)


def test_entry_basis_returns_the_structured_basis_of_the_opening_buy():
    store = new_store()
    pid = buy_prop(store, 2500.0)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)
    b = basis(store)
    assert b["ok"] and b["atr"] == 2500.0 and b["proposal_id"] == pid and b["ledger_qty"] == 10 and b["ledger_id"] and b["avg_price"] == 100_000.0


def test_entry_basis_rejects_quantity_mismatch_and_external_trades():
    store = new_store()
    pid = buy_prop(store)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)
    assert not basis(store, qty=9)["ok"] and "≠ 토스 보유" in basis(store, qty=9)["reason"]       # 외부에서 일부 매도
    assert not basis(store, qty=11)["ok"]                                                            # 외부에서 추가 매수
    assert not basis(store, qty=0)["ok"]


def test_entry_basis_new_position_after_full_exit_uses_the_new_proposal():
    store = new_store()
    old, new = buy_prop(store, 2000.0), buy_prop(store, 3000.0)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, old)
    store.add_ledger(CODE, "SELL", 10, 105_000.0, 0, None)                                           # 전량 매도 → 재고 0
    store.add_ledger(CODE, "BUY", 10, 120_000.0, 0, new)
    b = basis(store, avg=120_000.0)
    assert b["ok"] and b["atr"] == 3000.0 and b["proposal_id"] == new
    partial = new_store(); p1 = buy_prop(partial, 2000.0)
    partial.add_ledger(CODE, "BUY", 10, 100_000.0, 0, p1); partial.add_ledger(CODE, "SELL", 4, 105_000.0, 0, None)
    pb = basis(partial, qty=6)
    assert pb["ok"] and pb["atr"] == 2000.0 and pb["ledger_qty"] == 6                                # 일부 매도 후에도 같은 포지션


def test_entry_basis_external_sell_and_rebuy_with_same_quantity_is_caught_by_average_price():
    store = new_store()
    pid = buy_prop(store)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)                                             # 시스템 매수 후 토스 앱에서 전량 매도·재매수(원장에는 없음)
    assert not basis(store, avg=130_000.0)["ok"] and "평균단가" in basis(store, avg=130_000.0)["reason"]
    assert basis(store, avg=100_900.0)["ok"] and not basis(store, avg=101_500.0)["ok"]                  # 허용 오차 1%


def test_entry_basis_refuses_unreconciled_negative_inventory_and_mixed_buys():
    store = new_store()
    pid = buy_prop(store)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)
    store.set_unreconciled(CODE, "확인 필요")
    assert not basis(store)["ok"] and "불일치" in basis(store)["reason"]
    store.clear_unreconciled(CODE)
    assert basis(store)["ok"]
    neg = new_store()                                                                                # 원장에 없는 재고를 판 기록(경고 없이 직접 넣어 재현)
    neg.db.execute("INSERT INTO ledger(ts,code,side,qty,price,fee,proposal_id) VALUES('2026-10-01T00:00:00',?,'SELL',3,100000,0,NULL)", (CODE,))
    neg.db.execute("INSERT INTO ledger(ts,code,side,qty,price,fee,proposal_id) VALUES('2026-10-02T00:00:00',?,'BUY',10,100000,0,?)", (CODE, buy_prop(neg)))
    assert not basis(neg)["ok"] and "음수" in basis(neg)["reason"]
    mixed = new_store()
    mixed.add_ledger(CODE, "BUY", 5, 100_000.0, 0, buy_prop(mixed)); mixed.add_ledger(CODE, "BUY", 5, 100_000.0, 0, buy_prop(mixed, 9000.0))
    assert not basis(mixed)["ok"] and "섞여" in basis(mixed)["reason"]
    manual = new_store()
    manual.add_ledger(CODE, "BUY", 10, 100_000.0, 0, None)
    assert not basis(manual)["ok"] and "제안 번호" in basis(manual)["reason"]


def test_entry_basis_validates_the_proposal_and_its_atr():
    for label, kw in {"sell side": {"side": "SELL"}, "other code": {"code": "000660"}, "no atr": {"sizing": {}}, "zero": {"atr": 0.0},
                      "negative": {"atr": -5.0}, "nan": {"atr": float("nan")}, "inf": {"atr": float("inf")}, "no sizing": {"sizing": None}}.items():
        store = new_store()
        pid = buy_prop(store, **kw)
        store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)
        assert not basis(store)["ok"], label
    store = new_store()
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, 999_999)                                         # 존재하지 않는 제안
    assert not basis(store)["ok"]


def test_entry_basis_finds_an_old_proposal_even_beyond_the_latest_100():
    store = new_store()
    pid = buy_prop(store, 2222.0)
    store.add_ledger(CODE, "BUY", 10, 100_000.0, 0, pid)
    for _ in range(120):
        buy_prop(store, 1.0, code="000660")
    assert len(store.proposals(limit=100)) == 100 and all(p["id"] != pid for p in store.proposals(limit=100))
    assert basis(store)["atr"] == 2222.0


def held_position(env, widen=False):
    e, store, fake = make(env, rule={"live": True}, live=True)
    pid = first_buy(e); e.approve(pid); e.sync_orders()
    qty = store.proposal(pid)["qty"]
    fill = float(store.db.execute("SELECT price FROM ledger WHERE code=?", (CODE,)).fetchone()["price"])
    fake.items = [{"symbol": CODE, "quantity": str(qty), "averagePurchasePrice": str(fill), "lastPrice": str(fill)}]
    entry_atr = float(store.proposal(pid)["sizing"]["atr"])
    if widen:                                                                                        # 진입 후 변동성이 커진 상황
        df = e.prov.df.copy()
        df.iloc[-15:, df.columns.get_loc("high")] *= 1.10; df.iloc[-15:, df.columns.get_loc("low")] *= 0.90
        e.prov.df = df
    return e, store, fake, pid, qty, fill, entry_atr


def sell_ev(screen):
    return {c["label"].split("(")[0]: c for c in screen[CODE]["sell"]["evidence"] if "label" in c}


def test_sell_checks_fix_stop_and_target_to_the_entry_atr_but_keep_trailing_on_current_atr(env):
    e, store, fake, pid, qty, fill, entry_atr = held_position(env, widen=True)
    fake.px = fill * 0.5                                                                             # 어떤 손절선이든 이탈하도록
    ev = sell_ev(e.generate())
    cur_atr = float(e.generate()[CODE]["atr"])
    assert cur_atr > entry_atr * 1.3                                                                 # 변동성이 실제로 커졌다
    stop, target, trail = ev["손절선 이탈"], ev["목표가 도달"], ev["트레일링 스탑"]
    assert f"손절선 {fill - 2.0 * entry_atr:,.0f}" in stop["value"] and f"진입 ATR {entry_atr:,.0f} 고정 · 제안 #{pid}" in stop["threshold"]
    assert f"목표 {fill + 3.5 * entry_atr:,.0f}" in target["value"] and f"제안 #{pid}" in target["threshold"]
    assert f"현재 ATR {cur_atr:,.0f}" in trail["threshold"] and "진입 ATR" not in trail["threshold"] and "20일 고점" in trail["threshold"]
    hi20 = float(e.prov.df["close"].tail(20).max())
    assert f"기준 {hi20 - 2.5 * cur_atr:,.0f}" in trail["value"]                                     # 트레일링은 현재 ATR 로 계산된다 (변수 분리)


def test_sell_checks_fall_back_to_current_atr_and_say_why_when_holdings_do_not_match_the_ledger(env):
    e, store, fake, pid, qty, fill, entry_atr = held_position(env, widen=True)
    fake.items[0]["quantity"] = str(qty + 3)                                                         # 앱 밖에서 더 샀다
    fake.px = fill * 0.5
    scr = e.generate()
    ev = sell_ev(scr)
    cur_atr = float(scr[CODE]["atr"])
    assert f"손절선 {fill - 2.0 * cur_atr:,.0f}" in ev["손절선 이탈"]["value"]
    assert "현재 ATR" in ev["손절선 이탈"]["threshold"] and "원장 재고" in ev["손절선 이탈"]["threshold"] and "고정" not in ev["손절선 이탈"]["threshold"]
    assert "제안 #" not in ev["목표가 도달"]["threshold"]


def test_entry_basis_after_a_negative_stretch_is_rebalanced_to_zero_a_new_position_is_trusted():
    store = new_store()
    a, b = buy_prop(store, 1000.0), buy_prop(store, 7000.0)
    ins = lambda side, qty, pid: store.db.execute("INSERT INTO ledger(ts,code,side,qty,price,fee,proposal_id) VALUES('2026-10-01T00:00:00',?,?,?,100000,0,?)", (CODE, side, qty, pid))
    ins("SELL", 3, None)                                                                             # 원장에 없는 재고 매도 → 음수
    assert not basis(store, qty=1)["ok"]
    ins("BUY", 3, a)                                                                                 # 정확히 0 으로 맞춰짐
    ins("BUY", 10, b)                                                                                # 그 뒤의 새 포지션은 이 제안만 본다
    got = basis(store, qty=10)
    assert got["ok"] and got["atr"] == 7000.0 and got["proposal_id"] == b
