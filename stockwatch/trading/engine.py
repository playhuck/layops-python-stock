"""트레이딩 엔진: 제안 생성(매수 보수적/매도 과감) → 사람이 승인/거절 → 실행(모의 또는 실주문) → 체결 동기화/보호주문.

설계 원칙
- 기본은 모의(dry-run). 실주문은 '전역 스위치 AND 종목별 스위치' 둘 다 켜졌을 때만.
- 사람이 누르는 승인 없이는 어떤 주문도 나가지 않는다 (예외: 체결 후 토스 서버 감시용 보호 OCO 등록, 종목별 protect 옵션).
- 모든 판단 근거는 정량 체크리스트(evidence)로 남긴다.
"""
import json
import math
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta

import pandas as pd

from .. import config, indicators as ind, sectors
from ..envfile import env
from ..signals import analyze
from . import risk
from .portfolio import PortfolioState
from .store import Store
from .toss import TossClient, TossError

_LOCK = threading.RLock()  # 엔진 인스턴스가 여러 개여도 승인/동기화는 프로세스 전체에서 직렬화
LIVE_FAIL_LIMIT = 3  # 연속 주문 실패 시 자동 킬스위치
STALE_APPROVING_SEC = 60  # 승인 처리 중 상태로 이보다 오래 남아 있으면 프로세스가 중간에 죽은 것으로 본다


def chk(label: str, passed: bool, value: str, threshold: str = "") -> dict:
    return {"label": label, "passed": bool(passed), "value": value, "threshold": threshold}


class Gateway:
    """TossClient 래퍼: 짧은 캐시로 호출 한도를 아끼고, 키/만료 상태를 노출한다."""
    TTL = 45

    def __init__(self, factory=TossClient):
        self._factory, self._client, self._cache = factory, None, {}

    def configured(self) -> bool:
        try:
            env("TOSS_CLIENT_ID"); env("TOSS_CLIENT_SECRET"); return True
        except KeyError:
            return False

    def expires(self) -> date | None:
        try:
            return datetime.strptime(env("TOSS_EXPIRED").replace("-", ".").strip(), "%Y.%m.%d").date()
        except (KeyError, ValueError):
            return None

    @property
    def client(self) -> TossClient:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def cached(self, key, fn, ttl=None):
        import time
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < (self.TTL if ttl is None else ttl):
            return hit[1]
        v = fn(); self._cache[key] = (time.time(), v); return v

    def invalidate(self):
        self._cache.clear()

    def buying_power(self) -> float:
        return self.cached("bp", self.client.buying_power)

    def kr_holdings(self) -> list[dict]:
        h = self.cached("hold", self.client.holdings)
        out = []
        for it in (h or {}).get("items", []):
            sym = str(it.get("symbol", ""))
            if sym.isdigit() and len(sym) == 6:
                out.append({"symbol": sym, "qty": int(float(it["quantity"])), "avg": float(it["averagePurchasePrice"]),
                            "last": float(it["lastPrice"])})
        return out

    def price(self, code: str) -> float:
        return self.client.prices([code])[code]  # 승인 직전 재검증용이라 캐시하지 않는다

    def status(self) -> dict:
        exp = self.expires()
        st = {"configured": self.configured(), "expires": exp.isoformat() if exp else None,
              "days_left": (exp - date.today()).days if exp else None, "connected": False, "error": None}
        if st["configured"]:
            try:
                st["buying_power"] = self.buying_power(); st["connected"] = True
            except Exception as e:  # 연결 실패도 화면에 그대로 보여준다
                st["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return st


class Engine:
    def __init__(self, store: Store, gateway: Gateway, cfg: dict, provider, notifier=None):
        self.store, self.gw, self.cfg, self.prov = store, gateway, cfg, provider
        self.notify = notifier or (lambda title, body: None)
        self.t = cfg.get("trading", {})
        self._holdings_known = True
        self.lock = _LOCK

    # ---- 전역 상태 ------------------------------------------------------------------------------
    def live_enabled(self) -> bool:
        return self.store.get("live_enabled", "0") == "1" and not self.killed()

    def killed(self) -> bool:
        return self.store.get("kill_switch", "0") == "1"

    def engage_kill(self, reason: str):
        self.store.set("kill_switch", "1"); self.store.set("kill_reason", reason)
        self.store.set("live_enabled", "0")
        self.store.audit("kill_switch", {"reason": reason})
        self.notify("🚨 킬스위치 작동: 자동매매 중단", reason[:120])

    def reset_kill(self):
        self.store.set("kill_switch", "0"); self.store.set("kill_reason", ""); self.store.set("fail_streak", "0")
        self.store.audit("kill_reset")

    def monthly_pnl(self) -> float:
        pnl = self.store.realized_month()
        try:
            names = {i["code"] for i in self.cfg["watchlist"]}
            pnl += sum((h["last"] - h["avg"]) * h["qty"] for h in self.gw.kr_holdings() if h["symbol"] in names)
        except Exception:
            pass  # 보유 조회 실패 시 실현손익만 사용
        return pnl

    def check_kill(self) -> bool:
        cap, pct = float(self.t.get("capital_krw", 0)), float(self.t.get("monthly_kill_pct", 5))
        if cap > 0 and not self.killed() and self.monthly_pnl() <= -cap * pct / 100:
            self.engage_kill(f"월 손실이 자본의 {pct}% 한도 도달 ({self.monthly_pnl():,.0f}원)")
        return self.killed()

    def risk_state(self) -> dict:
        cap = float(self.t.get("capital_krw", 0))
        return {"live_enabled": self.live_enabled(), "live_switch": self.store.get("live_enabled", "0") == "1",
                "kill_switch": self.killed(), "kill_reason": self.store.get("kill_reason", ""),
                "capital": cap, "monthly_pnl": round(self.monthly_pnl()), "monthly_limit": -round(cap * float(self.t.get("monthly_kill_pct", 5)) / 100),
                "spent_today": self.store.spent_today(), "daily_limit": int(self.t.get("daily_buy_limit_krw", 0))}

    # ---- 시장 국면 ------------------------------------------------------------------------------
    def regime(self) -> tuple[bool, str]:
        idx = self.prov.table("index", "KOSPI")
        if idx is None or len(idx) < 60:
            return False, "KOSPI 데이터 부족"
        c = idx["clsprc_idx"]; ma60 = c.tail(60).mean()
        return bool(c.iloc[-1] >= ma60), f"KOSPI {c.iloc[-1]:,.0f} / 60일선 {ma60:,.0f}"

    # ---- 제안 생성 ------------------------------------------------------------------------------
    def portfolio_state(self, holdings: list[dict] | None, exclude_id: int | None = None) -> PortfolioState:
        """실제 보유 + 진행 중 매수 주문(예약)으로 만든 포트폴리오. exclude_id 는 '지금 승인하려는 자기 자신'을 예약에서 빼기 위한 것."""
        hold = {h["symbol"]: h["qty"] * h["last"] for h in (holdings or [])}
        return PortfolioState(hold, self.store.reserved_by_code(exclude_id), float(self.store.spent_today(exclude_id)),
                              uncertain_cash=self.store.reserved_cash_uncertain(exclude_id))

    def generate(self) -> dict:
        """갱신 직후 호출. 종목별 체크리스트를 계산하고, 조건 충족 시 제안을 만든다. 반환: 종목별 screen 결과.
        순서: ① 모든 종목 개별 평가(저장 없음) → ② 우선순위 정렬 → ③ 가상 포트폴리오에 하나씩 배정하며 한도 검사 → ④ 선정분만 한 번에 저장.
        (예전에는 평가하면서 바로 저장해서, 같은 보유 스냅샷으로 여러 종목이 동시에 통과해 최대 종목 수·업종 한도를 넘길 수 있었다)"""
        with self.lock:
            self.gw.invalidate()  # 제안은 항상 최신 보유/잔고 기준 (캐시로 방금 체결된 종목을 놓치지 않도록)
            self.check_kill()
            hlist, hold_err = [], None
            self._holdings_known = True
            if self.gw.configured():
                try:
                    hlist = self.gw.kr_holdings()
                except Exception as e:
                    hold_err = f"{type(e).__name__}: {str(e)[:100]}"
                    self._holdings_known = False  # 보유 현황을 모르면 중복 매수 위험 → 매수 제안 금지
            holdings = {h["symbol"]: h for h in hlist}
            vix, screen = self.prov.vix(), {}
            for item in self.cfg["watchlist"]:
                code = item["code"]
                try:
                    screen[code] = self._screen_one(item, holdings.get(code), vix)
                except (FileNotFoundError, ValueError) as e:
                    screen[code] = {"error": str(e)}
            self._assign_buys([o for o in screen.values() if "_ctx" in o], hlist)
            for o in screen.values():
                o.pop("_ctx", None)   # DataFrame 이 들어 있어 저장/직렬화 전에 제거
            if hold_err:
                screen["_warning"] = f"토스 보유 조회 실패 → 매도 제안 불가 ({hold_err})"
            self.store.set("last_screen", json.dumps(screen, ensure_ascii=False, default=str))
            return screen

    def _levels(self, code: str) -> tuple[float, float, float]:
        r = self.store.rule(code)
        return (float(r["stop_atr"] or self.t.get("stop_atr", 2.0)), float(r["target_atr"] or self.t.get("target_atr", 3.5)),
                float(self.t.get("trail_atr", 2.5)))

    def _screen_one(self, item: dict, held: dict | None, vix) -> dict:
        code, name = item["code"], item["name"]
        df = self.prov.prices(code)
        res = analyze(code, name, df, self.prov.flows(code), vix, self.cfg.get("thresholds"))
        d = ind.add_all(df); last = d.iloc[-1]
        atr, close = float(last["atr"]), float(last["close"])
        if not (math.isfinite(atr) and math.isfinite(close) and close > 0):
            raise ValueError(f"가격/ATR 을 계산할 수 없어요(종가 {close}, ATR {atr}). 시세 데이터를 확인하세요")   # generate 가 이 종목을 error 로 표시하고 제안에서 제외
        ref = close
        if self.gw.configured():
            try:
                ref = self.gw.cached(f"px{code}", lambda: self.gw.price(code), ttl=20)
                if not (math.isfinite(ref) and ref > 0):
                    ref = close                      # 토스 현재가가 비정상이면 종가로 대체
            except Exception:
                pass
        out = {"name": name, "price": ref, "atr": round(atr, 1), "buy": None, "sell": None}
        if held and held["qty"] > 0:
            out["sell"] = self._sell_checks(code, name, res, d, held, ref, atr)
        else:  # 매수 후보: 개별 조건만 먼저 계산해 두고, 포트폴리오 한도는 _assign_buys 에서 우선순위대로 배정하며 검사한다
            out["_ctx"] = {"item": item, "res": res, "d": d, "ref": ref, "atr": atr, "rule": self.store.rule(code),
                           "base": self._buy_base(item, res, d, ref, atr, vix)}
        return out

    # -- 매수 ① 종목 개별 조건 (포트폴리오와 무관) ------------------------------------------------
    def _buy_base(self, item, res, d, ref, atr, vix) -> list[dict]:
        code, t = item["code"], self.t
        last, ev = d.iloc[-1], []
        slope = ind.slope_pct(d["ma20"], 5)
        ev.append(chk("기존 판정이 '매수 적기'", res.verdict == "매수 적기", res.verdict, "매수 적기"))
        ev.append(chk("매수 점수", res.buy_score >= t.get("buy_min_score", 65), str(res.buy_score), f">= {t.get('buy_min_score', 65)}"))
        ev.append(chk("20일선 우상향", slope > 0, f"{slope:+.2f}%/일", "> 0"))
        ev.append(chk("60·120일선 위", last["close"] > last["ma60"] and last["close"] > last["ma120"],
                      f"종가 {last['close']:,.0f} / 60일 {last['ma60']:,.0f} / 120일 {last['ma120']:,.0f}", "종가 > 두 선"))
        ev.append(chk("RSI 과열 아님", last["rsi"] < t.get("buy_max_rsi", 65), f"{last['rsi']:.0f}", f"< {t.get('buy_max_rsi', 65)}"))
        ext = (ref - last["ma20"]) / atr
        ev.append(chk("20일선 이격(추격매수 방지)", ext <= 1.5, f"{ext:+.1f} ATR", "<= +1.5 ATR"))
        vd = getattr(self.prov, "vix_date", lambda: None)()    # 01시 제안은 직전 미국 마감 값을 쓴다 → 기준일을 근거에 같이 보여준다
        ev.append(chk("VIX 안정", vix is not None and vix < t.get("buy_max_vix", 25),
                      "없음" if vix is None else f"{vix:.1f}" + (f" ({vd:%m/%d} 마감)" if vd else ""), f"< {t.get('buy_max_vix', 25)}"))
        fl = self.prov.flows(code)
        smart = None if fl is None or fl.empty else int(fl.tail(7)[["institution", "foreign"]].sum().sum())
        ev.append(chk("7일 기관+외국인 순매수", smart is not None and smart > 0, "데이터 없음" if smart is None else f"{smart:+,}주", "> 0"))
        if t.get("regime_filter", True):
            ok, msg = self.regime(); ev.append(chk("시장 국면(KOSPI ≥ 60일선)", ok, msg, "≥ 60일선"))
        ev.append(chk("보유 현황 확인됨(중복 매수 방지)", self._holdings_known, "확인" if self._holdings_known else "토스 조회 실패", "확인"))
        inflight = self.store.in_flight(code, "BUY")
        ev.append(chk("진행 중 매수 주문 없음", not inflight, "없음" if not inflight else "접수/처리/확인 필요", "없음"))
        ev.append(chk("킬스위치 해제 상태", not self.killed(), self.store.get("kill_reason", "정상") or "정상", "OFF"))
        rej = self.store.rejected_today(code, "BUY")
        ev.append(chk("오늘 거절한 제안 아님", not rej, "거절함" if rej else "해당 없음", "같은 날 재제안 안 함"))
        return ev

    # -- 매수 ② 포트폴리오 한도 (생성 단계와 승인 단계가 같은 함수를 쓴다) --------------------------
    def _portfolio_checks(self, code: str, new_val: float, state: PortfolioState, cap: float, cls: dict) -> list[dict]:
        """보유 종목 수 + 업종(대/중분류) 쏠림. new_val 은 지금 사려는 금액. state 는 실제 보유·진행 주문·(생성 중) 가상 선정분을 모두 포함한다."""
        t = self.t
        others = state.codes() - {code}
        n_real, n_virt = len(state.real_codes() - {code}), len(set(state.virtual) - state.real_codes() - {code})
        mx = int(t.get("max_positions", 3))
        ev = [chk("보유 종목 수 한도", len(others) + 1 <= mx,
                  f"보유·주문 {n_real}종목" + (f" + 이번 배치 선정 {n_virt}종목" if n_virt else "") + f" + 이번 종목 = {len(others) + 1}종목", f"<= {mx}")]
        if cap <= 0:
            return ev
        # 업종 쏠림: 이 종목이 속한 모든 업종(대분류·중분류)에서 '보유·주문액 + 지금 사려는 금액'이 자본에서 차지하는 비중을 각각 한도와 비교한다.
        # 한도: 업종별 개별 설정 > (대분류만) 기본 한도. 가장 빠듯한 업종을 근거로 표시한다.
        default = float(t.get("max_group_pct", 40))
        limits = self.cfg.get("sector_limits", {})
        rows = []   # (한도 대비 사용률, 업종명, 사용 비중%, 기존 비중%, 한도%)
        for sid, info in cls.get(code, {}).get("sec", {}).items():
            lim = limits.get(sid, default if info["depth"] == 0 else None)
            if lim is None:
                continue
            peer = sum(state.exposure(c) for c in others if sid in cls.get(c, {}).get("sec", {}))
            pct = (peer + new_val) / cap * 100
            rows.append((pct / lim, info["name"], pct, peer / cap * 100, lim))
        manual = {i["code"]: i.get("group") for i in self.cfg["watchlist"]}
        if rows:
            ratio, nm, pct, peer_pct, lim = max(rows)
            ev.append(chk(f"업종 쏠림({nm}) 보유+신규", all(r[0] <= 1 for r in rows),
                          f"{pct:.0f}% (보유·주문·선정 {peer_pct:.0f}% + 신규 {new_val / cap * 100:.0f}%)", f"<= {lim:g}%"))
        elif manual.get(code):   # 토스 분류가 없고 수동 업종만 있는 종목: 같은 수동 업종끼리만 묶어서 기본 한도로 검사
            peer = sum(state.exposure(c) for c in others if manual.get(c) == manual[code])
            pct = (peer + new_val) / cap * 100
            ev.append(chk(f"업종 쏠림({manual[code]}) 보유+신규", pct <= default, f"{pct:.0f}% (보유·주문·선정 {peer / cap * 100:.0f}% + 신규 {new_val / cap * 100:.0f}%)", f"<= {default:g}%"))
        else:  # 분류를 모르면 쏠림을 검사할 수 없으므로 보수적으로 통과시키지 않는다
            ev.append(chk("업종 분류 확인(쏠림 검사)", False, "분류 데이터 없음 — 업종 동기화 필요", "분류됨"))
        return ev

    def _assign_buys(self, outs: list[dict], hlist: list[dict]):
        """개별 조건을 평가한 매수 후보들을 결정적 우선순위(점수↓ → ATR%↑ → 코드순)로 가상 포트폴리오에 하나씩 배정한다.
        앞순위가 선정되면 가상 보유가 늘어 뒤순위의 종목 수·업종·현금·일일 한도 검사에 반영된다. 선정분만 한 번에 저장한다."""
        t = self.t
        cap = float(t.get("capital_krw", 0))
        state = self.portfolio_state(hlist)
        codes = {o["_ctx"]["item"]["code"] for o in outs} | {i["code"] for i in self.cfg["watchlist"]} | state.codes()
        cls = sectors.classify(list(codes), self.store.db)
        ranked = sorted(outs, key=lambda o: (-o["_ctx"]["res"].buy_score, o["_ctx"]["atr"] / o["_ctx"]["ref"], o["_ctx"]["item"]["code"]))
        n_pass = sum(1 for o in ranked if all(e["passed"] for e in o["_ctx"]["base"]))
        selected, rank = [], 0
        for o in ranked:
            c = o["_ctx"]; code = c["item"]["code"]; ev = list(c["base"])
            if all(e["passed"] for e in ev):
                rank += 1
                ev.append(chk("동시 후보 우선순위", True, f"{rank}위 / {n_pass}개 (점수 {c['res'].buy_score}, ATR {c['atr'] / c['ref'] * 100:.1f}%)", "점수↓ → ATR%↑ → 코드순"))
            sz = self._size(code, c["ref"], c["atr"], cap, c["rule"], state)
            ev += self._portfolio_checks(code, sz.amount if sz.qty >= 1 else 0, state, cap, cls)
            ev.append(chk("사이징(한도금액·리스크) 수량 ≥ 1주", sz.qty >= 1, f"{sz.qty}주 ({sz.binding}에서 제한)", ">= 1주"))
            if sz.qty >= 1:
                ev.append(chk("손절 시 예상 손실", sz.max_loss <= cap * t.get("risk_per_trade_pct", 1.0) / 100 * 1.001,
                              f"{sz.max_loss:,}원 (자본의 {sz.max_loss / cap * 100:.2f}%)", f"<= {t.get('risk_per_trade_pct', 1.0)}%"))
            passed = all(e["passed"] for e in ev)
            o["buy"] = {"passed": passed, "evidence": ev, "proposal_id": None}
            if passed:
                state.add_virtual(code, sz.amount)
                selected.append((o, c, sz))
        self._persist_buys(selected)

    def _persist_buys(self, selected: list):
        items, outs = [], []
        for o, c, sz in selected:
            code = c["item"]["code"]
            live = self.store.get("live_enabled", "0") == "1" and bool(c["rule"]["live"])
            items.append({"code": code, "name": c["item"]["name"], "side": "BUY", "qty": sz.qty, "price": risk.round_tick(c["ref"], self._market(code), "up"),
                          "stop": sz.stop, "target": sz.target, "evidence": o["buy"]["evidence"], "sizing": {**asdict(sz), "atr": c["atr"]}, "dry_run": not live})
            outs.append(o)
        # 이전 배치의 대기 제안(보유로 전환된 종목 포함)을 모두 만료시키고 새 배치로 한 번에 교체
        ids = self.store.replace_pending("BUY", [i["code"] for i in self.cfg["watchlist"]], items)
        for it, o, pid in zip(items, outs, ids):
            o["buy"]["proposal_id"] = pid
            ok = sum(1 for e in it["evidence"] if e["passed"])
            self.notify(f"매수 제안: {it['name']}", f"승인 대기 중 · 근거 {ok}/{len(it['evidence'])} 충족{' · 모의' if it['dry_run'] else ''}")

    # -- 매도: 하나라도 걸리면 (과감) ------------------------------------------------------------
    def _sell_checks(self, code, name, res, d, held, ref, atr) -> dict:
        t, last = self.t, d.iloc[-1]
        stop_k, target_k, trail_k = self._levels(code)
        avg = held["avg"]; hi20 = float(d["close"].tail(20).max())
        ev = [
            chk("손절선 이탈(진입가-ATR배수)", ref <= avg - stop_k * atr, f"현재 {ref:,.0f} / 손절선 {avg - stop_k * atr:,.0f}", f"ATR×{stop_k}"),
            chk("트레일링 스탑(20일 고점-ATR배수)", ref <= hi20 - trail_k * atr, f"현재 {ref:,.0f} / 기준 {hi20 - trail_k * atr:,.0f}", f"ATR×{trail_k}"),
            chk("20일선 이탈", last["close"] < last["ma20"], f"종가 {last['close']:,.0f} / 20일선 {last['ma20']:,.0f}", "종가 < 20일선"),
            chk("매도 점수", res.sell_score >= t.get("sell_min_score", 25), str(res.sell_score), f">= {t.get('sell_min_score', 25)}"),
            chk("RSI 과매수", last["rsi"] >= 70, f"{last['rsi']:.0f}", ">= 70"),
            chk("목표가 도달(진입가+ATR배수)", ref >= avg + target_k * atr, f"현재 {ref:,.0f} / 목표 {avg + target_k * atr:,.0f}", f"ATR×{target_k}"),
        ]
        triggered = any(e["passed"] for e in ev) and not self.store.in_flight(code, "SELL")
        pid = None
        if triggered:
            ev.insert(0, chk("보유 수량/평균단가", True, f"{held['qty']}주 @ {avg:,.0f} (수익률 {(ref / avg - 1) * 100:+.1f}%)"))
            price = risk.round_tick(ref * 0.995, self._market(code), "down")  # 과감: 체결 우선(현재가보다 0.5% 낮게 지정가)
            pid = self._propose(code, name, "SELL", held["qty"], price, None, None, ev, None, self.store.rule(code))
        else:
            self.store.expire_pending(code, "SELL")
        return {"passed": triggered, "evidence": ev, "proposal_id": pid}

    def _market(self, code: str) -> str:
        return config.market_of(self.cfg, code)

    def _size(self, code, ref, atr, cap, rule, state: PortfolioState) -> risk.Sizing:
        t = self.t
        stop_k, target_k, _ = self._levels(code)
        bp = None
        if self.gw.configured():
            try:
                bp = max(self.gw.buying_power() - state.virtual_total() - state.uncertain_cash, 0)   # 같은 배치의 앞선 선정분 + 승인 중·확인 필요 주문이 쓸 현금은 뺀다
            except Exception:
                bp = 0  # 잔고를 확인할 수 없으면 사지 않는다 (보수적)
        daily = max(float(t.get("daily_buy_limit_krw", 0)) - state.daily_total(), 0)
        return risk.position_size(
            entry=ref, atr=atr, capital=cap, budget=float(rule["budget_krw"]), held_value=0.0, buying_power=bp,
            daily_remaining=daily, max_order=float(t.get("max_order_krw", 0)) or None, risk_pct=float(t.get("risk_per_trade_pct", 1.0)),
            max_gap_loss_pct=float(t.get("max_gap_loss_pct", 3.0)), gap_assumption_pct=float(t.get("gap_assumption_pct", 15.0)),
            stop_k=stop_k, target_k=target_k, market=self._market(code))

    def _propose(self, code, name, side, qty, price, stop, target, ev, sizing, rule) -> int | None:
        # 오늘 이미 거절한 제안은 같은 날 반복해서 띄우지 않는다 (알림 피로 방지)
        rej = self.store.db.execute("SELECT 1 FROM proposals WHERE code=? AND side=? AND status='rejected' AND substr(decided_at,1,10)=?",
                                    (code, side, datetime.now().date().isoformat())).fetchone()
        if rej:
            return None
        self.store.expire_pending(code, side)
        live = self.store.get("live_enabled", "0") == "1" and bool(rule["live"])
        pid = self.store.add_proposal(code=code, name=name, side=side, qty=qty, price=price, stop=stop, target=target,
                                      evidence=ev, sizing=sizing, dry_run=not live)
        ok = sum(1 for c in ev if c["passed"])
        self.notify(f"{'매수' if side == 'BUY' else '매도'} 제안: {name}",
                    f"승인 대기 중 · 근거 {ok}/{len(ev)} 충족{' · 모의' if not live else ''}")
        return pid

    def removal_blockers(self, code: str) -> list[str]:
        """관심 종목에서 뺄 수 없는 사유. 보유/주문/보호주문이 걸린 종목을 목록에서 지우면 관리(매도 제안·체결 동기화)가 끊긴다."""
        why = []
        if self.store.in_flight(code, "BUY") or self.store.in_flight(code, "SELL"):
            why.append("진행 중인 주문이 있습니다")
        if self._protected(code):
            why.append("보호주문(손절/익절)이 걸린 보유 종목입니다")
        if self.gw.configured():
            try:
                self.gw.invalidate()
                if any(h["symbol"] == code and h["qty"] > 0 for h in self.gw.kr_holdings()):
                    why.append("토스에 보유 중인 종목입니다 (먼저 매도하세요)")
            except Exception as e:
                why.append(f"보유 여부를 확인할 수 없습니다 ({type(e).__name__})")
        return why

    # ---- 승인/거절 ------------------------------------------------------------------------------
    def reject(self, pid: int) -> dict:
        ok = self.store.transition(pid, "pending", "rejected")
        self.store.audit("reject", {"id": pid, "ok": ok})
        return {"ok": ok, "error": None if ok else "대기 중인 제안이 아닙니다"}

    def approve(self, pid: int) -> dict:
        with self.lock:
            p = self.store.proposal(pid)
            if not p:
                return {"ok": False, "error": "제안 없음"}
            if not self.store.transition(pid, "pending", "approving", attempt_mode="unknown"):  # 더블클릭/중복 호출 차단 + 시도 모드는 아직 모름(주문 직전에 확정 기록)
                return {"ok": False, "error": f"이미 처리된 제안입니다 ({p['status']})"}
            try:
                return self._execute(p)
            except Exception as e:  # 예기치 못한 오류도 상태에 남기고 실패 카운트
                return self._fail(p, f"{type(e).__name__}: {str(e)[:160]}")

    def _fail(self, p, msg: str) -> dict:
        self.store.transition(p["id"], "approving", "failed", result=json.dumps({"error": msg}, ensure_ascii=False))
        streak = int(self.store.get("fail_streak", "0")) + 1
        self.store.set("fail_streak", str(streak))
        self.store.audit("order_failed", {"id": p["id"], "error": msg, "streak": streak})
        if streak >= LIVE_FAIL_LIMIT:
            self.engage_kill(f"주문 연속 {streak}회 실패: 시스템/API 점검 필요 ({msg})")
        return {"ok": False, "error": msg}

    def _block(self, p, msg: str) -> dict:
        self.store.transition(p["id"], "approving", "blocked", result=json.dumps({"error": msg}, ensure_ascii=False))
        self.store.audit("blocked", {"id": p["id"], "reason": msg})
        return {"ok": False, "error": msg, "blocked": True}

    def _execute(self, p) -> dict:
        code, side, rule = p["code"], p["side"], self.store.rule(p["code"])
        if self.check_kill():
            return self._block(p, f"킬스위치 작동 중: {self.store.get('kill_reason')}")
        live = self.store.get("live_enabled", "0") == "1" and bool(rule["live"])
        if not live:  # 모의: 주문 전송 없이 의도만 기록
            self.store.set_attempt(p["id"], "dry-run", p["qty"], p["price"])
            self.store.transition(p["id"], "approving", "simulated", result=json.dumps(
                {"mode": "dry-run", "would_send": {"symbol": code, "side": side, "qty": p["qty"], "price": p["price"]}}, ensure_ascii=False))
            self.store.audit("simulated", {"id": p["id"]})
            return {"ok": True, "mode": "dry-run"}
        if not self.gw.configured():
            return self._block(p, "토스 API 키 미설정")
        px = self.gw.price(code)
        drift = abs(px / p["price"] - 1) * 100
        if drift > float(self.t.get("max_price_drift_pct", 2.0)):
            return self._block(p, f"제안가 대비 현재가 {drift:.1f}% 변동 → 다음 갱신에서 재제안 필요 (현재 {px:,.0f}원)")
        qty, price = p["qty"], p["price"]
        if side == "BUY":
            # 최종 관문: 제안은 오래됐을 수 있고, 그사이 다른 제안이 승인·체결됐을 수 있다. 최신 보유와 진행 주문(자기 자신 제외)으로 한도를 다시 검사한다.
            self.gw.invalidate()
            hl = self.gw.kr_holdings()
            cap = float(self.t.get("capital_krw", 0))
            if cap <= 0:
                return self._block(p, "자본금이 0이라 주문할 수 없어요 (설정에서 자본금을 정하세요)")
            if any(h["symbol"] == code and h["qty"] > 0 for h in hl):
                return self._block(p, "이미 보유 중인 종목이에요")
            if self.store.in_flight(code, "BUY", exclude_id=p["id"]):
                return self._block(p, "같은 종목의 매수 주문이 이미 진행 중이에요")
            state = self.portfolio_state(hl, exclude_id=p["id"])
            bp = self.gw.buying_power() - state.uncertain_cash   # 승인 중·확인 필요 주문(자기 제외)이 쓸 현금은 뺀다
            price = risk.round_tick(px, self._market(code), "up")
            daily_left = max(float(self.t.get("daily_buy_limit_krw", 0)) - state.daily_used, 0)   # state 는 자기 자신을 뺀 사용액
            amount_cap = min(bp, daily_left, float(rule["budget_krw"]))
            qty = min(qty, int(amount_cap // price))  # 승인 시점 기준으로 다시 줄일 수만 있다 (늘리지 않는다)
            if qty < 1:
                return self._block(p, f"주문 가능 금액 부족 (매수가능 {bp:,.0f} / 일일잔여 {daily_left:,.0f} / 한도 {rule['budget_krw']:,})")
            cls = sectors.classify([code, *state.codes()], self.store.db)
            bad = [c for c in self._portfolio_checks(code, qty * price, state, cap, cls) if not c["passed"]]
            if bad:
                return self._block(p, "포트폴리오 한도 초과: " + " / ".join(f"{c['label']} {c['value']} (기준 {c['threshold']})" for c in bad))
        else:
            held = next((h for h in self.gw.kr_holdings() if h["symbol"] == code), None)
            if not held:
                return self._block(p, "보유 수량 없음")
            qty, price = min(qty, held["qty"]), risk.round_tick(px * 0.995, self._market(code), "down")
        open_orders = self.gw.client.open_orders(code)
        if open_orders:   # 보호주문을 건드리기 전에 막는다(예전에는 해제 후 차단되어 포지션이 무보호로 남을 수 있었다)
            return self._block(p, f"{code} 미체결 주문이 이미 있습니다 (중복 방지)")
        # ★ 주문을 보내기 직전에 '모드·확정 수량·가격'을 영구 저장: 이후 프로세스가 죽어도 무엇을 보내려 했는지 정확히 알 수 있다
        self.store.set_attempt(p["id"], "live", qty, price)
        if side == "SELL":
            self._cancel_protection(code)  # 보호 OCO 가 있으면 먼저 해제 (이중 매도 방지)
        try:
            r = self.gw.client.create_order(symbol=code, side=side, quantity=qty, price=price, client_order_id=f"sw{p['id']}")
        except TossError as e:
            if side == "SELL":
                self._restore_protection(code)
            if e.status in (400, 409, 422):  # 장 마감/잔고 부족/호가 오류 등 업무 규칙 거절: 시스템 장애가 아니므로 실패 카운트(킬스위치)에 넣지 않는다
                return self._block(p, f"토스 주문 거절: {e}")
            return self._fail(p, str(e))
        self.store.set("fail_streak", "0")
        self.gw.invalidate()
        self.store.transition(p["id"], "approving", "ordered", order_id=r["orderId"], qty=qty, price=price,
                              result=json.dumps({"mode": "live", "orderId": r["orderId"]}))
        self.store.audit("ordered", {"id": p["id"], "orderId": r["orderId"], "side": side, "qty": qty, "price": price})
        return {"ok": True, "mode": "live", "order_id": r["orderId"]}

    # ---- 승인 도중 중단된 제안 복구 ------------------------------------------------------------------
    def reconcile_approving(self) -> list[str]:
        """승인 처리 중(approving)에 프로세스가 죽어 남은 제안을 정리한다. 판단 기준은 '지금의 스위치'가 아니라 주문 직전에 저장한 시도 기록이다.
        - attempt_mode = dry-run : 전송이 없었으므로 실패 처리
        - attempt_mode = live    : 토스 주문 목록에서 종목·방향·수량·지정가가 모두 같고 시각이 맞는 주문이 정확히 하나일 때만 이어받음. 아니면 확인 필요
        - attempt_mode = unknown : 무엇을 시도했는지 알 수 없으므로 무조건 확인 필요 (추측하지 않는다)
        조회용 주문에는 clientOrderId 가 없고 멱등키 보존기간도 스펙에 없어서 같은 키로 재전송하는 방식은 쓰지 않는다(자동 재전송 금지)."""
        notes = []
        with self.lock:
            for p in self.store.stale_approving(STALE_APPROVING_SEC):
                mode = p.get("attempt_mode") or "unknown"
                if mode == "dry-run":
                    self.store.transition(p["id"], "approving", "failed", result=json.dumps({"error": "모의 승인 처리 중 중단됨 (주문 전송 없음)"}, ensure_ascii=False))
                    self.store.audit("approving_recovered", {"id": p["id"], "to": "failed", "mode": mode})
                    continue
                m = self._find_sent_order(p) if mode == "live" and self.gw.configured() else None
                if m:
                    self.store.transition(p["id"], "approving", "ordered", order_id=m["orderId"], qty=p["attempt_qty"], price=p["attempt_price"],
                                          result=json.dumps({"mode": "live", "orderId": m["orderId"], "recovered": True}))
                    self.store.audit("approving_recovered", {"id": p["id"], "to": "ordered", "orderId": m["orderId"]})
                    notes.append(f"{p['code']} 승인 중 중단된 주문을 토스에서 찾아 이어받았어요")
                else:
                    warn = " (매도 전 보호주문이 해제됐을 수 있어요)" if p["side"] == "SELL" else ""
                    why = "시도 내용을 알 수 없어요" if mode == "unknown" else "토스에서 같은 주문을 하나로 특정하지 못했어요"
                    self.store.transition(p["id"], "approving", "needs_review", result=json.dumps({"error": f"주문이 나갔는지 확인이 필요해요 — {why}{warn}"}, ensure_ascii=False))
                    self.store.audit("approving_recovered", {"id": p["id"], "to": "needs_review", "mode": mode})
                    notes.append(f"{p['code']} 주문 전송 여부 확인 필요{warn}")
        return notes

    def _toss_orders_since(self, p) -> list[dict]:
        t0 = p.get("attempt_at") or p["decided_at"]
        return self.gw.client.open_orders(p["code"]) + self.gw.client.closed_orders(p["code"], t0[:10])

    def _find_sent_order(self, p) -> dict | None:
        """이 제안이 낸 주문을 토스 주문 목록에서 찾는다. 종목·방향·수량·지정가가 모두 같고 시각이 맞는 주문이 정확히 하나일 때만 반환."""
        if p.get("attempt_qty") is None or p.get("attempt_price") is None or not p.get("attempt_at"):
            return None
        try:
            orders = self._toss_orders_since(p)
        except Exception:
            return None
        used = {x["order_id"] for x in self.store.proposals(limit=500) if x["order_id"]}
        t0 = datetime.fromisoformat(p["attempt_at"]).astimezone()
        hits = []
        for o in orders:
            try:
                same = (o.get("side") == p["side"] and str(o.get("symbol")) == p["code"] and o.get("orderId") not in used
                        and int(float(o["quantity"])) == int(p["attempt_qty"]) and int(float(o["price"])) == int(p["attempt_price"]))
                ot = datetime.fromisoformat(o["orderedAt"])
            except (KeyError, ValueError, TypeError):
                continue
            if same and t0 - timedelta(seconds=10) <= ot <= t0 + timedelta(minutes=10):
                hits.append(o)
        return hits[0] if len(hits) == 1 else None

    def review_candidates(self, pid: int) -> list[dict]:
        """확인 필요 제안의 후보 주문: 시도 시각 이후 같은 종목·방향의 토스 주문(미체결+당일 종료). 사용자가 맞는 것을 고른다.
        exact = 수량·지정가까지 시도 기록과 일치. 토스 API 의 orderId 는 앱에 보이지 않으므로 사용자가 입력하게 하지 않고 목록에서 고르게 한다."""
        p = self.store.proposal(pid)
        if not p or p["status"] != "needs_review" or not self.gw.configured():
            return []
        try:
            orders = self._toss_orders_since(p)
        except Exception:
            return []
        used = {x["order_id"] for x in self.store.proposals(limit=500) if x["order_id"]}
        t0 = datetime.fromisoformat(p.get("attempt_at") or p["decided_at"]).astimezone()
        out = []
        for o in orders:
            try:
                ot = datetime.fromisoformat(o["orderedAt"])
                if o.get("side") != p["side"] or str(o.get("symbol")) != p["code"] or o.get("orderId") in used or ot < t0 - timedelta(seconds=10):
                    continue
                q, pr = int(float(o["quantity"])), int(float(o["price"]))
            except (KeyError, ValueError, TypeError):
                continue
            out.append({"orderId": o["orderId"], "quantity": q, "price": pr, "status": o.get("status"), "orderedAt": o["orderedAt"],
                        "exact": q == p.get("attempt_qty") and pr == p.get("attempt_price")})
        return sorted(out, key=lambda x: x["orderedAt"])

    def resolve_review(self, pid: int, resolution: str, order_id: str | None = None) -> dict:
        """확인 필요 제안을 사람이 확정한다.
        adopt        : 후보 주문 중 하나를 골라 편입 → ordered 로 정상 동기화(원장·보호주문·손익 추적이 복구됨)
        not_sent     : 나가지 않았음 → failed (토스에 미체결·일치 주문이 있으면 거부, 매도면 보호주문 복구)
        unreconciled : 체결됐지만 후보에 없음 → 닫고 '원장 미반영 가능성' 경고를 남김(보유가 0이 되면 자동 해제)
        확인할 수 없으면 거부한다(안전 우선)."""
        with self.lock:
            p = self.store.proposal(pid)
            if not p or p["status"] != "needs_review":
                return {"ok": False, "error": "확인 필요 상태의 제안이 아니에요"}
            if resolution not in ("adopt", "not_sent", "unreconciled"):
                return {"ok": False, "error": "알 수 없는 선택이에요"}
            try:
                cands = self.review_candidates(pid)
                open_ = self.gw.client.open_orders(p["code"])
            except Exception as e:
                return {"ok": False, "error": f"토스 주문을 확인하지 못해 확정할 수 없어요 ({type(e).__name__})"}
            if resolution == "adopt":
                c = next((x for x in cands if x["orderId"] == order_id), None)
                if not c:
                    return {"ok": False, "error": "선택한 주문이 후보 목록에 없어요"}
                self.store.transition(pid, "needs_review", "ordered", order_id=c["orderId"], qty=c["quantity"], price=c["price"],
                                      result=json.dumps({"mode": "live", "orderId": c["orderId"], "manual_adopt": True}))
                self.store.audit("review_resolved", {"id": pid, "resolution": "adopt", "orderId": c["orderId"]})
                self.gw.invalidate()
                return {"ok": True, "status": "ordered"}
            if open_:
                return {"ok": False, "error": "토스에 이 종목의 미체결 주문이 남아 있어요. 주문이 나간 것이니 '이 주문이에요'로 편입하거나, 체결·취소된 뒤에 확정하세요"}
            if any(x["exact"] for x in cands):
                return {"ok": False, "error": "토스에 수량·가격이 같은 주문이 있어요. 나가지 않았다고 확정할 수 없어요 — '이 주문이에요'로 편입하세요"}
            if resolution == "not_sent":
                if p["side"] == "SELL":
                    try:
                        self._restore_protection(p["code"])   # 매도 전에 해제된 보호주문을 되돌린다
                    except Exception:
                        pass
                self.store.transition(pid, "needs_review", "failed", result=json.dumps({"manual": "수동 확인: 주문이 전송되지 않았음"}, ensure_ascii=False))
                self.store.audit("review_resolved", {"id": pid, "resolution": "not_sent"})
                self.gw.invalidate()
                return {"ok": True, "status": "failed"}
            note = (f"{p['code']} {'매수' if p['side'] == 'BUY' else '매도'} 주문이 체결됐다고 확인했지만 토스 주문 목록에서 찾지 못해 원장에 반영하지 못했어요. "
                    "토스 체결 내역과 보호주문(손절/익절)을 직접 확인하세요")
            self.store.transition(pid, "needs_review", "closed", result=json.dumps({"manual": "수동 확인: 체결됐으나 원장 미반영"}, ensure_ascii=False))
            # BUY: 보유가 정리되면 사라지는 position 경고. SELL: 보유가 이미 0일 수 있어도 원장(실현손익·킬스위치)이 틀린 채로 남으므로 사용자가 닫을 때까지 유지
            self.store.set_unreconciled(p["code"], note, kind="position" if p["side"] == "BUY" else "ledger")
            self.store.audit("review_resolved", {"id": pid, "resolution": "unreconciled", "side": p["side"]})
            self.gw.invalidate()
            return {"ok": True, "status": "closed"}

    # ---- 체결 동기화 + 보호 OCO ------------------------------------------------------------------
    def sync_orders(self) -> list[str]:
        """'ordered' 상태 주문의 체결을 확인해 원장에 기록하고, 매수 체결분에는 보호 OCO(손절/익절)를 건다."""
        notes = []
        with self.lock:
            notes += self.reconcile_approving()   # 승인 도중 중단돼 남은 제안부터 정리
            if not self.gw.configured():
                return notes
            for p in self.store.proposals("ordered"):
                try:
                    o = self.gw.client.order(p["order_id"])
                except TossError as e:
                    notes.append(f"{p['code']} 주문 조회 실패: {e}"); continue
                st, ex = o.get("status"), o.get("execution") or {}
                filled = int(float(ex.get("filledQuantity") or 0))
                if st in ("CANCELED", "REJECTED") and not filled:
                    self.store.transition(p["id"], "ordered", "closed", result=json.dumps({"status": st})); continue
                if st != "FILLED" or filled <= 0:
                    continue  # 미체결/부분체결: 다음 동기화에서 다시 확인
                avg_px = float(ex.get("averageFilledPrice") or p["price"])
                fee = float(ex.get("commission") or 0) + float(ex.get("tax") or 0)
                self.store.add_ledger(p["code"], p["side"], filled, avg_px, fee, p["id"])
                prot = None
                if p["side"] == "BUY" and self.store.rule(p["code"])["protect"]:
                    prot = self._register_protection(p, filled, avg_px)
                    notes.append(f"{p['code']} 매수 체결 {filled}주 @ {avg_px:,.0f} → 보호주문 {'등록' if prot else '실패(수동 확인 필요)'}")
                else:
                    notes.append(f"{p['code']} {p['side']} 체결 {filled}주 @ {avg_px:,.0f}")
                self.store.transition(p["id"], "ordered", "filled", protect_order_id=prot,
                                      result=json.dumps({"filled": filled, "avg": avg_px, "fee": fee}))
            notes += self._sync_protection()
            self.gw.invalidate()
            try:
                held = {h["symbol"] for h in self.gw.kr_holdings() if h["qty"] > 0}
                for code, info in self.store.unreconciled().items():
                    if info.get("kind", "position") == "position" and code not in held:
                        self.store.clear_unreconciled(code)    # 포지션이 정리되면 확인 필요 경고도 사라진다
            except Exception:
                pass
            self.check_kill()
        if notes:
            self.notify("체결/보호주문 알림", " / ".join(n[:60] for n in notes[:3]))
        return notes

    def _sync_protection(self) -> list[str]:
        """보호 OCO 가 발동했으면(서버가 손절/익절 실행) 체결 매도를 원장에 기록한다. 이게 빠지면 킬스위치가 손실을 못 본다."""
        notes = []
        for x in self.store.proposals("filled"):
            if x["side"] != "BUY" or not x["protect_order_id"]:
                continue
            try:
                c = self.gw.client.conditional_order(x["protect_order_id"])
            except TossError as e:
                notes.append(f"{x['code']} 보호주문 조회 실패: {e}"); continue
            if c.get("status") not in ("COMPLETED", "EXPIRED"):
                continue
            leg = next((c[k] for k in ("first", "second") if (c.get(k) or {}).get("triggeredOrderId")), None)
            if leg:
                try:
                    o = self.gw.client.order(leg["triggeredOrderId"]); ex = o.get("execution") or {}
                    q = int(float(ex.get("filledQuantity") or 0))
                    if q > 0:
                        self.store.add_ledger(x["code"], "SELL", q, float(ex.get("averageFilledPrice") or 0),
                                              float(ex.get("commission") or 0) + float(ex.get("tax") or 0), x["id"])
                        notes.append(f"{x['code']} 보호주문 발동 → 매도 체결 {q}주 @ {float(ex.get('averageFilledPrice') or 0):,.0f}")
                    elif o.get("status") not in ("FILLED",):
                        continue  # 발동했지만 아직 미체결: 다음 동기화에서 재확인 (갭하락 시 손절 지정가가 안 닿을 수 있음)
                except TossError as e:
                    notes.append(f"{x['code']} 보호주문 체결 조회 실패: {e}"); continue
            else:
                notes.append(f"{x['code']} 보호주문 만료 → 재등록 필요 (현재 무보호 상태)")
            self.store.db.execute("UPDATE proposals SET protect_order_id=NULL WHERE id=?", (x["id"],))
            self.store.audit("protect_done", {"id": x["id"], "status": c.get("status")})
        return notes

    def _register_protection(self, p, qty: int, fill_px: float) -> str | None:
        mkt = self._market(p["code"])
        atr = (p["sizing"] or {}).get("atr") or 0
        stop_k, target_k, _ = self._levels(p["code"])
        stop, target = (p["stop"], p["target"]) if not atr else risk.atr_levels(fill_px, atr, stop_k, target_k, mkt)
        if not (stop and target and stop < fill_px < target):
            self.store.audit("protect_skipped", {"id": p["id"], "stop": stop, "target": target, "fill": fill_px}); return None
        try:
            r = self.gw.client.create_oco(
                symbol=p["code"], quantity=qty, target_trigger=target, target_price=target,
                stop_trigger=stop, stop_price=risk.round_tick(stop * 0.985, mkt, "down"),  # 손절 지정가는 감시가 1.5% 아래(체결 확률↑)
                expire=date.today() + timedelta(days=30), client_order_id=f"swp{p['id']}")
            self.store.audit("protect_registered", {"id": p["id"], "oco": r.get("conditionalOrderId"), "stop": stop, "target": target})
            return r.get("conditionalOrderId")
        except TossError as e:
            self.store.audit("protect_failed", {"id": p["id"], "error": str(e)}); return None

    def _protected(self, code: str) -> list[dict]:
        return [x for x in self.store.proposals("filled") if x["code"] == code and x["side"] == "BUY" and x["protect_order_id"]]

    def _cancel_protection(self, code: str):
        for x in self._protected(code):
            try:
                self.gw.client.cancel_conditional(x["protect_order_id"])
            except TossError as e:
                if e.status != 404:  # 이미 발동/취소된 경우(404)는 무시
                    raise
            self.store.db.execute("UPDATE proposals SET protect_order_id=NULL WHERE id=?", (x["id"],))
            self.store.audit("protect_cancelled", {"id": x["id"]})

    def _restore_protection(self, code: str):
        """매도 주문이 실패했는데 보호 OCO 는 이미 해제된 경우: 보유분에 보호를 다시 건다."""
        for x in [y for y in self.store.proposals("filled") if y["code"] == code and y["side"] == "BUY"][:1]:
            held = next((h for h in self.gw.kr_holdings() if h["symbol"] == code), None)
            if held:
                oid = self._register_protection(x, held["qty"], held["avg"])
                self.store.db.execute("UPDATE proposals SET protect_order_id=? WHERE id=?", (oid, x["id"]))


