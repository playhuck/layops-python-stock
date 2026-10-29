"""트레이딩 상태 저장소 (PostgreSQL). 제안/규칙/원장/감사로그/전역 스위치/푸시 토큰.
시각 컬럼은 ISO 문자열(TEXT)로 유지한다: 화면/기존 로직이 문자열 슬라이스에 의존하기 때문."""
import json
from datetime import datetime

from .. import db as dbm


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, database: dbm.Database | None = None):
        self.db = database or dbm.get()   # execute(sql, params) / tx() 제공

    # -- 전역 설정: 기본값은 항상 안전한 쪽(실주문 OFF, 킬스위치 OFF) -------------------------------
    def get(self, key: str, default: str = "") -> str:
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key: str, value: str):
        self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (key, value))

    def audit(self, event: str, detail=None):
        self.db.execute("INSERT INTO audit(ts,event,detail) VALUES(?,?,?)",
                        (now(), event, json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None))

    def add_push_token(self, token: str):
        self.db.execute("INSERT INTO push_tokens(token,created_at) VALUES(?,?) ON CONFLICT DO NOTHING", (token, now()))

    def push_token_count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) AS c FROM push_tokens").fetchone()["c"])

    # -- 규칙 -------------------------------------------------------------------------------------
    def rule(self, code: str) -> dict:
        r = self.db.execute("SELECT * FROM rules WHERE code=?", (code,)).fetchone()
        return dict(r) if r else {"code": code, "budget_krw": 0, "live": 0, "protect": 1, "stop_atr": None, "target_atr": None}

    def save_rule(self, code: str, **f):
        cur = self.rule(code); cur.update({k: v for k, v in f.items() if k in cur})
        self.db.execute("INSERT INTO rules(code,budget_krw,live,protect,stop_atr,target_atr) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(code) DO UPDATE SET budget_krw=EXCLUDED.budget_krw, live=EXCLUDED.live, "
                        "protect=EXCLUDED.protect, stop_atr=EXCLUDED.stop_atr, target_atr=EXCLUDED.target_atr",
                        (code, int(cur["budget_krw"]), int(bool(cur["live"])), int(bool(cur["protect"])),
                         cur["stop_atr"], cur["target_atr"]))

    # -- 제안 -------------------------------------------------------------------------------------
    def add_proposal(self, **p) -> int:
        r = self.db.execute(
            "INSERT INTO proposals(created_at,code,name,side,status,qty,price,stop,target,evidence,sizing,dry_run) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (now(), p["code"], p.get("name"), p["side"], "pending", p["qty"], p["price"], p.get("stop"), p.get("target"),
             json.dumps(p["evidence"], ensure_ascii=False), json.dumps(p.get("sizing"), ensure_ascii=False), int(p["dry_run"])))
        return int(r.fetchone()["id"])

    def proposal(self, pid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return self._prop(r) if r else None

    @staticmethod
    def _prop(r) -> dict:
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"]); d["sizing"] = json.loads(d["sizing"]) if d["sizing"] else None
        return d

    def proposals(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q, a = "SELECT * FROM proposals", ()
        if status:
            q += " WHERE status=?"; a = (status,)
        return [self._prop(r) for r in self.db.execute(q + " ORDER BY id DESC LIMIT ?", a + (limit,))]

    def transition(self, pid: int, frm: str, to: str, **extra) -> bool:
        """원자적 상태 전이(UPDATE ... WHERE status=frm). 이미 다른 상태면 False → 더블클릭/중복 호출이 두 번 실행되지 않는다."""
        sets = ["status=?", "decided_at=?"] + [f"{k}=?" for k in extra]
        cur = self.db.execute(f"UPDATE proposals SET {','.join(sets)} WHERE id=? AND status=?", (to, now(), *extra.values(), pid, frm))
        return cur.rowcount == 1

    def expire_pending(self, code: str, side: str):
        self.db.execute("UPDATE proposals SET status='expired', decided_at=? WHERE status='pending' AND code=? AND side=?", (now(), code, side))

    # -- 원장 -------------------------------------------------------------------------------------
    def add_ledger(self, code, side, qty, price, fee=0.0, proposal_id=None):
        if side == "SELL":
            inv = self.db.execute("SELECT COALESCE(SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END),0) AS n FROM ledger WHERE code=?", (code,)).fetchone()["n"]
            if int(inv) < qty:
                self.set_unreconciled(code, f"원장에 매수 기록이 없거나 부족한 상태로 매도 {qty}주가 체결됐어요 (원장 재고 {int(inv)}주). 실현손익이 정확히 계산되지 않아요 — 토스 체결 내역과 대조하세요", kind="ledger")
        self.db.execute("INSERT INTO ledger(ts,code,side,qty,price,fee,proposal_id) VALUES(?,?,?,?,?,?,?)",
                        (now(), code, side, qty, price, fee, proposal_id))

    # 진행 중 매수 주문으로 간주하는 상태: 승인 중 / 접수(체결 대기) / 전송 여부 확인 필요
    RESERVING = "('approving','ordered','needs_review')"

    def filled_today(self) -> float:
        """오늘 체결된 매수액 (원장 기준)."""
        r = self.db.execute("SELECT COALESCE(SUM(qty*price),0) AS s FROM ledger WHERE side='BUY' AND substr(ts,1,10)=?", (now()[:10],)).fetchone()
        return float(r["s"])

    def reserved_today(self, exclude_id: int | None = None) -> float:
        """오늘 접수·승인된 매수 중 아직 체결 동기화 전인 주문액. exclude_id(자기 자신)는 빼고 계산한다.
        체결이 동기화되면 제안 상태가 filled 로 바뀌고 원장에 기록되므로 filled_today 와 겹치지 않는다."""
        r = self.db.execute(f"SELECT COALESCE(SUM(COALESCE(attempt_qty,qty)*COALESCE(attempt_price,price)),0) AS s FROM proposals WHERE side='BUY' AND status IN {self.RESERVING} "
                            "AND substr(decided_at,1,10)=? AND id<>?", (now()[:10], -1 if exclude_id is None else exclude_id)).fetchone()
        return float(r["s"])

    def spent_today(self, exclude_id: int | None = None) -> int:
        """오늘 매수에 쓴(또는 묶인) 금액 = 체결 + 예약. exclude_id 를 주면 그 제안 자신은 예약에서 뺀다 (자기 금액이 이중 차감되는 것 방지)."""
        return int(self.filled_today() + self.reserved_today(exclude_id))

    def available_daily(self, limit: float, exclude_id: int | None = None) -> float:
        return max(float(limit) - self.spent_today(exclude_id), 0.0)

    def reserved_by_code(self, exclude_id: int | None = None) -> dict[str, float]:
        """종목별 진행 중 매수 주문액(날짜 무관: 접수만 되고 동기화가 안 된 주문은 날짜가 지나도 노출로 본다)."""
        rows = self.db.execute(f"SELECT code, SUM(COALESCE(attempt_qty,qty)*COALESCE(attempt_price,price)) AS s FROM proposals WHERE side='BUY' AND status IN {self.RESERVING} AND id<>? GROUP BY code",
                               (-1 if exclude_id is None else exclude_id,)).fetchall()
        return {r["code"]: float(r["s"]) for r in rows}

    def reserved_cash_uncertain(self, exclude_id: int | None = None) -> float:
        """토스 매수가능금액에 아직 반영되지 않았을 수 있는 예약: 승인 중(approving) + 전송 여부 불확실(needs_review).
        접수된 주문(ordered)은 토스가 이미 매수가능금액에서 뺐다고 가정한다(첫 실주문에서 전후 비교로 확인 필요 — 미검증).
        틀려도 토스가 잔고 부족 주문을 거절하므로 초과 체결은 일어나지 않는다."""
        r = self.db.execute("SELECT COALESCE(SUM(COALESCE(attempt_qty,qty)*COALESCE(attempt_price,price)),0) AS s FROM proposals "
                            "WHERE side='BUY' AND status IN ('approving','needs_review') AND id<>?", (-1 if exclude_id is None else exclude_id,)).fetchone()
        return float(r["s"])

    def set_attempt(self, pid: int, mode: str, qty: int | None = None, price: int | None = None):
        """주문을 보내기 '직전'에 모드와 확정 수량·가격을 영구 저장한다. 이후 프로세스가 죽어도 무엇을 보내려 했는지 알 수 있다."""
        self.db.execute("UPDATE proposals SET attempt_mode=?, attempt_qty=?, attempt_price=?, attempt_at=? WHERE id=?", (mode, qty, price, now(), pid))

    # -- 원장 불일치 경고(원장에 반영되지 않은 체결 가능성) --------------------------------------------------
    def set_unreconciled(self, code: str, note: str, kind: str = "position"):
        """kind: 'position' = 보유가 0이 되면 자동 해제 / 'ledger' = 과거 손익 계산이 부정확해진 것이라 사용자가 확인하고 직접 닫는다."""
        self.set(f"unreconciled:{code}", json.dumps({"note": note, "kind": kind, "at": now()}, ensure_ascii=False))

    def clear_unreconciled(self, code: str):
        self.db.execute("DELETE FROM settings WHERE key=?", (f"unreconciled:{code}",))

    def unreconciled(self) -> dict[str, dict]:
        rows = self.db.execute("SELECT key, value FROM settings WHERE key LIKE ?", ("unreconciled:%",)).fetchall()
        return {r["key"].split(":", 1)[1]: json.loads(r["value"]) for r in rows}

    def in_flight(self, code: str, side: str, exclude_id: int | None = None) -> bool:
        return self.db.execute(f"SELECT 1 AS x FROM proposals WHERE code=? AND side=? AND status IN {self.RESERVING} AND id<>? LIMIT 1",
                               (code, side, -1 if exclude_id is None else exclude_id)).fetchone() is not None

    def rejected_today(self, code: str, side: str) -> bool:
        return self.db.execute("SELECT 1 AS x FROM proposals WHERE code=? AND side=? AND status='rejected' AND substr(decided_at,1,10)=? LIMIT 1",
                               (code, side, now()[:10])).fetchone() is not None

    def replace_pending(self, side: str, codes: list[str], items: list[dict]) -> list[int]:
        """대기 제안을 한 트랜잭션으로 교체: codes 의 기존 대기 제안을 만료시키고 items 를 새로 저장한다.
        (만료만 되고 저장이 실패하거나, 이전 배치와 새 배치가 섞여 보이는 상태를 막는다)"""
        ids = []
        with self.db.tx() as c:
            c.execute("UPDATE proposals SET status='expired', decided_at=%s WHERE status='pending' AND side=%s AND code = ANY(%s)", (now(), side, list(codes)))
            for p in items:
                r = c.execute(
                    "INSERT INTO proposals(created_at,code,name,side,status,qty,price,stop,target,evidence,sizing,dry_run) "
                    "VALUES(%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (now(), p["code"], p.get("name"), p["side"], p["qty"], p["price"], p.get("stop"), p.get("target"),
                     json.dumps(p["evidence"], ensure_ascii=False), json.dumps(p.get("sizing"), ensure_ascii=False), int(p["dry_run"]))).fetchone()
                ids.append(int(r["id"]))
        return ids

    def stale_approving(self, seconds: int = 60) -> list[dict]:
        """승인 처리 중이던 채로 남은 제안(프로세스가 중간에 죽은 경우)."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
        return [self._prop(r) for r in self.db.execute("SELECT * FROM proposals WHERE status='approving' AND decided_at < ? ORDER BY id", (cutoff,))]

    def realized_month(self) -> float:
        """이번 달 실현손익(평균단가법, 수수료 포함). 원장에 기록된 체결만 반영."""
        pos: dict[str, list[float]] = {}  # code -> [qty, cost]
        total = 0.0
        for r in self.db.execute("SELECT * FROM ledger ORDER BY id"):
            q, c = pos.setdefault(r["code"], [0, 0.0])
            if r["side"] == "BUY":
                pos[r["code"]] = [q + r["qty"], c + r["qty"] * r["price"] + r["fee"]]
            elif q > 0:
                avg = c / q
                m = min(r["qty"], q)                      # 원장에 있는 재고만큼만 손익 계산(나머지는 불일치 경고로 표시됨)
                pnl = m * (r["price"] - avg) - r["fee"]
                pos[r["code"]] = [q - m, c - avg * m]
                if r["ts"][:7] == now()[:7]:
                    total += pnl
        return total
