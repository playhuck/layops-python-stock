"""0단계 회귀 테스트: 동시 제안·연속 승인에서도 최대 종목 수 / 업종 / 일일 한도 / 현금이 보장되는가."""
import threading

import numpy as np
import pytest
from pgtest import new_store

from stockwatch.signals import Result
from stockwatch.trading import engine as eng
from stockwatch.trading.engine import Engine, Gateway
from test_trading import FakeClient, FakeProvider

A, B, C = "005930", "000660", "353200"
SCORES = {A: 70, B: 80, C: 75}          # 우선순위: B(80) > C(75) > A(70)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x"); monkeypatch.setenv("TOSS_CLIENT_SECRET", "y"); monkeypatch.setenv("TOSS_EXPIRED", "2027.10.29")
    monkeypatch.setattr(eng, "analyze", lambda code, name, *a, **k: Result(code, name, 100000, SCORES.get(code, 70), 0, "매수 적기"))


def build(trading=None, codes=(A, B), groups=None, live=True):
    groups = groups or {c: "반도체" for c in codes}
    cfg = {"watchlist": [{"code": c, "name": f"종목{c}", "group": groups[c]} for c in codes],
           "trading": {"capital_krw": 10_000_000, "max_order_krw": 0, "buy_max_rsi": 101, "buy_min_score": 65,
                       "daily_buy_limit_krw": 9_000_000, "max_positions": 3, "max_group_pct": 100, **(trading or {})}}
    store, fake = new_store(), FakeClient()
    for c in codes:
        store.save_rule(c, budget_krw=3_000_000, live=live)
    if live:
        store.set("live_enabled", "1")
    return Engine(store, Gateway(lambda: fake), cfg, FakeProvider()), store, fake


def pending(store): return {p["code"]: p for p in store.proposals("pending")}


def failing(screen, code): return [c["label"] for c in screen[code]["buy"]["evidence"] if not c["passed"]]


def test_max_positions_limits_simultaneous_proposals_and_picks_highest_priority(env):
    e, store, fake = build({"max_positions": 1})
    scr = e.generate()
    assert list(pending(store)) == [B]                                   # 점수 80 인 B 하나만
    assert "보유 종목 수 한도" in failing(scr, A)                          # 탈락 사유가 화면 근거에 남는다
    assert any(c["label"] == "동시 후보 우선순위" and c["value"].startswith("1위") for c in scr[B]["buy"]["evidence"])


def test_group_limit_applies_across_the_same_batch(env):
    e, store, fake = build({"max_group_pct": 25})                        # 같은 업종 제안 2건(각 16%) = 32% > 25%
    scr = e.generate()
    assert len(pending(store)) == 1 and any(l.startswith("업종 쏠림") for l in failing(scr, A))


def test_different_sectors_can_both_be_proposed(env):
    e, store, fake = build({"max_group_pct": 25}, groups={A: "반도체", B: "로봇"})
    e.generate()
    assert set(pending(store)) == {A, B}


def test_selection_does_not_depend_on_watchlist_order(env):
    picked = []
    for order in ((A, B, C), (C, B, A), (B, A, C)):
        e, store, fake = build({"max_positions": 2}, codes=order)
        e.generate(); picked.append(set(pending(store)))
    assert picked[0] == picked[1] == picked[2] == {B, C}


def test_cash_is_not_double_spent_across_candidates_in_a_batch(env):
    e, store, fake = build()
    fake.bp = 2_000_000.0
    e.generate()
    total = sum(p["qty"] * p["price"] for p in pending(store).values())
    assert 0 < total <= 2_000_000                                        # 후보 2개가 같은 현금을 각각 쓰지 않는다


def test_regenerating_replaces_the_previous_batch_atomically(env):
    e, store, fake = build()
    e.generate(); first = {p["id"] for p in store.proposals("pending")}
    e.generate(); second = {p["id"] for p in store.proposals("pending")}
    assert first.isdisjoint(second) and len(second) == len(first) == 2
    assert all(p["status"] == "expired" for p in store.proposals("expired"))


# ---- 승인 단계 최종 재검증 ---------------------------------------------------------------------
def test_approving_second_proposal_is_blocked_when_max_positions_would_be_exceeded(env):
    e, store, fake = build()
    e.generate(); ids = [p["id"] for p in sorted(store.proposals("pending"), key=lambda p: p["id"])]
    e.t["max_positions"] = 1                                             # 생성 이후 설정이 바뀌었거나, 제안이 오래된 상황
    r1, r2 = e.approve(ids[0]), e.approve(ids[1])
    assert r1["ok"] and not r2["ok"] and r2.get("blocked") and "보유 종목 수" in r2["error"]
    assert len(fake.orders) == 1


def test_approving_second_proposal_is_blocked_by_sector_limit(env):
    e, store, fake = build()
    e.generate(); ids = [p["id"] for p in store.proposals("pending")]
    e.t["max_group_pct"] = 25
    results = [e.approve(i) for i in ids]
    assert sum(r["ok"] for r in results) == 1 and len(fake.orders) == 1
    assert any("업종 쏠림" in r.get("error", "") for r in results if not r["ok"])


def test_concurrent_approvals_cannot_both_pass_the_position_limit(env):
    e, store, fake = build()
    e.generate(); ids = [p["id"] for p in store.proposals("pending")]
    e.t["max_positions"] = 1
    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(e.approve(i))) for i in ids]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(fake.orders) == 1 and sorted(r["ok"] for r in out) == [False, True]


def test_daily_limit_equal_to_proposal_amount_is_fully_executable(env):
    # 이전 버그: 승인 중인 자기 금액이 사용액에 이미 들어가 한도=제안금액이면 차단, 한도 250만이면 16주→9주로 줄었다
    for limit in (1_600_000, 2_500_000, 4_000_000):
        e, store, fake = build({"daily_buy_limit_krw": limit}, codes=(A,))
        e.generate(); p = store.proposals("pending")[0]
        r = e.approve(p["id"])
        assert r["ok"] and fake.orders[-1]["quantity"] == p["qty"], f"한도 {limit:,}: {r}"


def test_daily_limit_is_shared_between_two_orders_without_exceeding(env):
    e, store, fake = build({"daily_buy_limit_krw": 2_500_000}, groups={A: "반도체", B: "로봇"})
    e.generate()
    for p in sorted(store.proposals("pending"), key=lambda p: p["id"]):
        e.approve(p["id"])
    assert 0 < sum(o["quantity"] * o["price"] for o in fake.orders) <= 2_500_000


def test_filled_but_unsynced_order_is_not_counted_twice_in_sector_exposure(env):
    # A 가 체결돼 토스 보유에 보이지만 아직 동기화 전(ordered). 보유(160만)+예약(160만)=320만으로 이중 집계하면 B 가 불필요하게 막힌다.
    e, store, fake = build({"max_group_pct": 35})
    e.generate(); pa = pending(store)[A]
    assert e.approve(pa["id"])["ok"]
    sent = fake.orders[-1]
    fake.items = [{"symbol": A, "quantity": str(sent["quantity"]), "averagePurchasePrice": str(sent["price"]), "lastPrice": str(sent["price"])}]
    e.gw.invalidate()
    e.generate()
    assert B in pending(store), failing(e.generate(), B)                 # max(보유, 예약) 규칙: 160만 + 신규 160만 = 32% ≤ 35%


# ---- 0d: 승인 도중 중단된 제안 복구 ---------------------------------------------------------------
from datetime import datetime, timedelta  # noqa: E402


def stuck(e, store, code, minutes=2, mode="live"):
    """승인 도중 프로세스가 죽은 상황: approving 인데 order_id 가 없고 오래됨. mode 는 주문 직전에 저장된 시도 기록(live/dry-run/unknown)."""
    e.generate(); p = pending(store)[code]
    assert store.transition(p["id"], "pending", "approving", attempt_mode="unknown")
    old = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    store.db.execute("UPDATE proposals SET decided_at=? WHERE id=?", (old, p["id"]))
    if mode != "unknown":
        store.set_attempt(p["id"], mode, p["qty"], p["price"])
        store.db.execute("UPDATE proposals SET attempt_at=? WHERE id=?", (old, p["id"]))
    return store.proposal(p["id"])


def toss_order(p, **kw):
    t = datetime.fromisoformat(p["attempt_at"] or p["decided_at"]) + timedelta(seconds=5)
    return {"orderId": "REC1", "symbol": p["code"], "side": p["side"], "quantity": str(p["qty"]), "price": str(p["price"]),
            "orderedAt": t.astimezone().isoformat(timespec="milliseconds"), **kw}


def test_stuck_approving_matching_toss_order_is_adopted(env):
    e, store, fake = build(codes=(A,))
    p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: [toss_order(p)]; fake.closed_orders = lambda symbol, day: []
    fake.order_state["REC1"] = {"status": "PENDING", "execution": {}}                       # 이어받은 주문은 아직 미체결
    e.sync_orders()
    q = store.proposal(p["id"])
    assert q["status"] == "ordered" and q["order_id"] == "REC1" and not fake.orders      # 이어받기만 하고 재전송은 하지 않는다


def test_stuck_approving_without_match_goes_to_review_and_keeps_reserving(env):
    e, store, fake = build(codes=(A, B), groups={A: "반도체", B: "로봇"})
    p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: []; fake.closed_orders = lambda symbol, day: []
    e.sync_orders()
    assert store.proposal(p["id"])["status"] == "needs_review" and not fake.orders         # 자동 재전송 금지
    assert store.reserved_by_code().get(A) == p["qty"] * p["price"]                         # 확인 전까지 예약 유지(보수적)
    scr = e.generate()
    assert "진행 중 매수 주문 없음" in failing(scr, A)                                         # 같은 종목 신규 제안 차단


def test_ambiguous_match_is_not_guessed(env):
    e, store, fake = build(codes=(A,))
    p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: [toss_order(p), toss_order(p, orderId="REC2")]; fake.closed_orders = lambda symbol, day: []
    e.sync_orders()
    assert store.proposal(p["id"])["status"] == "needs_review"


def test_resolve_requires_no_open_order_at_toss(env):
    e, store, fake = build(codes=(A,))
    p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: []; fake.closed_orders = lambda symbol, day: []
    e.sync_orders()
    fake.open_orders = lambda symbol=None: [toss_order(p)]                                  # 토스에 미체결이 남아 있으면 '미전송'으로 못 닫는다
    assert not e.resolve_review(p["id"], "not_sent")["ok"] and store.proposal(p["id"])["status"] == "needs_review"
    fake.open_orders = lambda symbol=None: []
    assert e.resolve_review(p["id"], "not_sent") == {"ok": True, "status": "failed"}
    assert store.reserved_by_code() == {}


def test_recent_approving_is_left_alone_and_dry_run_attempt_is_failed(env):
    e, store, fake = build(codes=(A,))
    p = stuck(e, store, A, minutes=0)                                                       # 방금 승인 시작: 건드리지 않는다
    e.sync_orders(); assert store.proposal(p["id"])["status"] == "approving"
    e2, store2, fake2 = build(codes=(A,)); p2 = stuck(e2, store2, A, mode="dry-run")        # 모의 시도였으면 전송이 없었다 → 실패 처리(마찰 없음)
    e2.sync_orders(); assert store2.proposal(p2["id"])["status"] == "failed" and not fake2.orders


def test_resolve_api_is_guarded_and_listed_in_state(env):
    from pgtest import test_db
    from stockwatch import db
    from stockwatch.trading import service
    from stockwatch.trading.store import Store
    from stockwatch.web import app
    db.use(test_db())                                                   # 실제 DB 대신 임시 스키마 + 가짜 브로커 (실제 토스 호출 방지)
    try:
        service._store, service._gateway = new_store(), Gateway(lambda: FakeClient())
        c = app.test_client()
        assert c.post("/api/trading/proposals/1/resolve", json={"resolution": "not_sent"}).status_code == 403
        assert c.get("/api/trading/state").json["needs_review"] == []
    finally:
        db.use(None)


# ---- 리뷰 1~4 재현 시나리오 회귀 테스트 --------------------------------------------------------------
def none_open(fake):
    fake.open_orders = lambda symbol=None: []; fake.closed_orders = lambda symbol, day: []


def test_recovery_follows_the_recorded_attempt_not_the_current_switch(env):
    # 1) 주문은 토스에 나가 있는데 재시작 후 실주문 스위치가 꺼진 상태: 예전에는 failed 로 닫히고 예약이 풀렸다
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: [toss_order(p)]; fake.closed_orders = lambda symbol, day: []; fake.order_state["REC1"] = {"status": "PENDING", "execution": {}}
    store.set("live_enabled", "0")
    e.sync_orders()
    assert store.proposal(p["id"])["status"] == "ordered" and store.reserved_by_code().get(A)
    # 시도 내용을 모르면(unknown) 스위치와 무관하게 무조건 확인 필요, 아무것도 추측하지 않는다
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A, mode="unknown"); none_open(fake); store.set("live_enabled", "0")
    e.sync_orders(); assert store.proposal(p["id"])["status"] == "needs_review"
    # 종목 스위치가 꺼진 모의 승인이 중단돼도 불필요한 '확인 필요'가 되지 않는다
    e, store, fake = build(codes=(A,)); store.save_rule(A, live=False); p = stuck(e, store, A, mode="dry-run"); none_open(fake)
    e.sync_orders(); assert store.proposal(p["id"])["status"] == "failed"


def test_approving_records_the_attempt_before_sending_and_dry_run_is_recorded(env):
    e, store, fake = build(codes=(A,)); e.generate(); p = store.proposals("pending")[0]
    seen = {}
    orig = fake.create_order
    def spy(**kw):                                                       # 주문이 나가는 순간 DB 에는 이미 확정 시도 기록이 있어야 한다
        seen.update(store.proposal(p["id"])); return orig(**kw)
    fake.create_order = spy
    assert e.approve(p["id"])["ok"]
    sent = fake.orders[-1]
    assert seen["attempt_mode"] == "live" and seen["attempt_qty"] == sent["quantity"] and seen["attempt_price"] == sent["price"] and seen["attempt_at"]
    e2, store2, fake2 = build(codes=(A,), live=False); e2.generate(); p2 = store2.proposals("pending")[0]
    assert e2.approve(p2["id"])["mode"] == "dry-run" and store2.proposal(p2["id"])["attempt_mode"] == "dry-run"


def test_unrelated_manual_order_is_never_adopted(env):
    # 2) 사용자가 토스 앱에서 직접 낸 3주 @5만원 주문을 제안(16주)의 것으로 이어받던 문제
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A)
    fake.open_orders = lambda symbol=None: [toss_order(p, quantity="3", price="50000", orderId="MANUAL1")]; fake.closed_orders = lambda symbol, day: []
    e.sync_orders()
    assert store.proposal(p["id"])["status"] == "needs_review" and store.proposal(p["id"])["order_id"] is None
    cand = e.review_candidates(p["id"])
    assert [(c["orderId"], c["exact"]) for c in cand] == [("MANUAL1", False)]               # 후보로는 보여주되 일치(exact)가 아니다


def test_resolve_by_picking_a_candidate_restores_ledger_and_protection(env):
    # 3) '전송됨'으로 그냥 닫아 원장·보호주문·손익이 비던 문제 → 후보 선택 편입 후 정상 동기화
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A)
    none_open(fake); e.sync_orders(); assert store.proposal(p["id"])["status"] == "needs_review"
    sent = toss_order(p, orderId="REAL9", quantity=str(p["qty"]))                           # 사실은 나가 있었고 시각만 매칭 창을 벗어난 경우
    sent["orderedAt"] = (datetime.fromisoformat(p["attempt_at"]) + timedelta(minutes=30)).astimezone().isoformat(timespec="milliseconds")
    fake.closed_orders = lambda symbol, day: [sent]
    fake.order_state["REAL9"] = {"status": "FILLED", "execution": {"filledQuantity": str(p["qty"]), "averageFilledPrice": str(p["price"]), "commission": "100", "tax": "0"}}
    assert not e.resolve_review(p["id"], "not_sent")["ok"]                                   # 일치 주문이 있는데 '안 나갔다'고 확정할 수는 없다
    assert not e.resolve_review(p["id"], "adopt", "NOPE")["ok"]
    assert e.resolve_review(p["id"], "adopt", "REAL9") == {"ok": True, "status": "ordered"}
    e.sync_orders()
    q = store.proposal(p["id"])
    assert q["status"] == "filled" and q["protect_order_id"]                                 # 보호 OCO 등록
    assert store.db.execute("select count(*) c from ledger where side='BUY'").fetchone()["c"] == 1


def test_unreconciled_is_flagged_and_auto_clears_when_flat(env):
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A); none_open(fake); e.sync_orders()
    assert e.resolve_review(p["id"], "unreconciled")["status"] == "closed" and A in store.unreconciled()
    fake.items = [{"symbol": A, "quantity": "5", "averagePurchasePrice": "1", "lastPrice": "1"}]; e.gw.invalidate()
    e.sync_orders(); assert A in store.unreconciled()                                        # 아직 보유 중이면 유지
    fake.items = []; e.gw.invalidate(); e.sync_orders(); assert A not in store.unreconciled()  # 포지션이 정리되면 사라진다


def test_unreconciled_sell_stays_flagged_even_when_position_is_flat(env):
    # 미확인 매도가 체결됐다면 보유는 이미 0 → position 경고는 바로 지워져 원장(손익·킬스위치) 오류가 묻힌다. SELL 은 ledger 로 남긴다
    e, store, fake = build(codes=(A,)); p = stuck(e, store, A)
    store.db.execute("UPDATE proposals SET side='SELL' WHERE id=?", (p["id"],))
    none_open(fake); e.sync_orders()
    assert store.proposal(p["id"])["status"] == "needs_review"
    assert e.resolve_review(p["id"], "unreconciled")["status"] == "closed"
    assert store.unreconciled()[A]["kind"] == "ledger"
    fake.items = []; e.gw.invalidate(); e.sync_orders(); e.sync_orders()
    assert A in store.unreconciled()                                                         # 보유가 없어도 사용자가 닫을 때까지 유지
    store.clear_unreconciled(A); assert A not in store.unreconciled()


def test_sell_fill_without_ledger_inventory_is_flagged_not_silently_ignored(env):
    e, store, fake = build(codes=(A,))
    store.add_ledger(A, "SELL", 16, 90_000.0, 0)                                              # 매수 기록 없이 매도 체결
    assert store.unreconciled()[A]["kind"] == "ledger" and "원장" in store.unreconciled()[A]["note"]
    store.add_ledger(B, "BUY", 10, 100.0, 0); store.add_ledger(B, "SELL", 15, 90.0, 0)       # 재고 10주인데 15주 매도: 재고만큼만 손익 계산 + 경고
    assert store.realized_month() == pytest.approx(10 * (90 - 100)) and B in store.unreconciled()


def test_uncertain_reservations_reduce_cash_for_other_candidates_and_for_approval(env):
    # 4) 확인 필요/승인 중 주문이 쓸 현금을 다른 종목이 다시 쓰던 문제
    tl_scores = dict(SCORES)
    try:
        SCORES.update({A: 70, B: 10})
        e, store, fake = build(codes=(A, B), groups={A: "반도체", B: "로봇"}); fake.bp = 2_000_000.0
        pa = stuck(e, store, A); none_open(fake); e.sync_orders()
        assert store.proposal(pa["id"])["status"] == "needs_review"
        SCORES[B] = 80; e.generate()
        pb = pending(store)[B]
        assert store.reserved_by_code()[A] + pb["qty"] * pb["price"] <= 2_000_000                # 합계가 토스 매수가능금액 이내
        # 이미 만들어진 제안이라도 승인 시점에 다시 줄어든다
        store.db.execute("UPDATE proposals SET qty=16 WHERE id=?", (pb["id"],))
        assert e.approve(pb["id"])["ok"] and fake.orders[-1]["quantity"] * fake.orders[-1]["price"] <= 400_000
    finally:
        SCORES.clear(); SCORES.update(tl_scores)


def test_blocked_sell_does_not_cancel_the_protection_order(env):
    # 보호주문을 먼저 해제한 뒤 '미체결 주문 있음'으로 막혀 포지션이 무보호가 되던 문제
    e, store, fake = build(codes=(A,)); e.generate(); pa = pending(store)[A]; assert e.approve(pa["id"])["ok"]; e.sync_orders()
    assert store.proposal(pa["id"])["protect_order_id"] == "C1"
    sent = fake.orders[-1]
    fake.items = [{"symbol": A, "quantity": str(sent["quantity"]), "averagePurchasePrice": str(sent["price"]), "lastPrice": "80000"}]; fake.px = 80_000.0; e.gw.invalidate()
    e.generate(); sell = [x for x in store.proposals("pending") if x["side"] == "SELL"][0]
    fake.open_orders = lambda symbol=None: [{"orderId": "OTHER"}]                              # 이미 미체결 주문이 있다
    r = e.approve(sell["id"])
    assert not r["ok"] and r.get("blocked") and fake.cancelled == []                           # 보호주문은 건드리지 않았다


def test_nan_price_or_atr_marks_the_stock_as_error_and_never_builds_a_proposal(env):
    # 마지막 봉 종가가 비어 있으면 가격·ATR 이 NaN → 이전에는 NaN 이 last_screen/응답에 그대로 실려 화면 전체가 죽었다
    e, store, fake = build(codes=(A,))
    df = FakeProvider().df.copy(); df.iloc[-1, df.columns.get_loc("close")] = np.nan
    e.prov = FakeProvider(df)
    screen = e.generate()
    assert "error" in screen[A] and "계산할 수 없" in screen[A]["error"]
    assert not store.proposals("pending")
    assert "NaN" not in (store.get("last_screen") or "")
