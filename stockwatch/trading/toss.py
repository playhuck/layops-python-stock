"""토스증권 Open API 클라이언트 (https://openapi.tossinvest.com, OAuth2 client credentials).

주의 (공식 스펙 기준)
- client 당 유효 토큰은 1개: 재발급하면 이전 토큰이 즉시 무효화된다 → 앱은 이 클라이언트 하나만, 단일 프로세스로 사용.
- 계좌 범위 API 는 X-Tossinvest-Account(accountSeq) 헤더가 필요하다.
- POST /orders 는 clientOrderId 가 멱등성 키: 같은 값으로 재전송해도 주문이 중복되지 않는다.
"""
import threading
import time
from datetime import datetime, date

import requests

from ..envfile import env

BASE = "https://openapi.tossinvest.com"


class TossError(RuntimeError):
    def __init__(self, status: int, code: str | None, message: str):
        super().__init__(f"HTTP {status} {code or ''} {message}".strip())
        self.status, self.code, self.message = status, code, message


class TossClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base: str = BASE, session: requests.Session | None = None):
        self._id = client_id or env("TOSS_CLIENT_ID")
        self._secret = client_secret or env("TOSS_CLIENT_SECRET")
        self.base = base
        self.s = session or requests.Session()
        self._tok: str | None = None
        self._tok_exp = 0.0
        self._lock = threading.Lock()
        self._account: int | None = None
        self._last_call = 0.0
        self.min_interval = 0.3  # 호출 간 최소 간격(초): 한도 수치가 공개 스펙에 없어 보수적으로 둔다

    # -- 인증 ------------------------------------------------------------------------------
    def _token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._tok or time.time() >= self._tok_exp:
                r = self.s.post(f"{self.base}/oauth2/token", timeout=15, data={
                    "grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret})
                if r.status_code != 200:
                    raise self._err(r)
                j = r.json()
                self._tok = j["access_token"]
                self._tok_exp = time.time() + max(int(j.get("expires_in", 300)) - 60, 30)
            return self._tok

    @staticmethod
    def _err(r: requests.Response) -> TossError:
        try:
            j = r.json()
            body = j.get("error") if isinstance(j.get("error"), dict) else j
            return TossError(r.status_code, body.get("code") or j.get("error"),
                             str(body.get("message") or j.get("error_description") or "")[:200])
        except ValueError:
            return TossError(r.status_code, None, r.text[:200])

    def _pace(self):
        with self._lock:
            wait = self._last_call + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _call(self, method: str, path: str, *, params=None, json=None, account: bool = False):
        refreshed, backoff = False, 0
        while True:
            h = {"Authorization": f"Bearer {self._token(force=False)}"}
            if account:
                h["X-Tossinvest-Account"] = str(self.account_seq())
            self._pace()
            r = self.s.request(method, self.base + path, params=params, json=json, headers=h, timeout=20)
            if r.status_code == 401 and not refreshed:
                refreshed = True
                self._tok = None
                continue  # 토큰 만료/무효화 → 1회 재발급 후 재시도 (401 은 미처리 상태)
            if r.status_code == 429 and backoff < 3:
                # 429 는 접수 전 거절이라 재시도 안전 (주문은 clientOrderId 멱등성도 있음)
                time.sleep(float(r.headers.get("Retry-After") or 2 ** backoff))
                backoff += 1
                continue
            if r.status_code >= 400:
                raise self._err(r)
            if r.status_code == 204 or not r.content:
                return None
            j = r.json()
            return j.get("result", j) if isinstance(j, dict) else j

    # -- 계좌 ------------------------------------------------------------------------------
    def accounts(self) -> list[dict]:
        return self._call("GET", "/api/v1/accounts")

    def account_seq(self) -> int:
        if self._account is None:
            acc = [a for a in self.accounts() if a.get("accountType") == "BROKERAGE"]
            if not acc:
                raise TossError(404, "no-brokerage-account", "종합매매 계좌 없음")
            self._account = int(acc[0]["accountSeq"])
        return self._account

    def buying_power(self, currency: str = "KRW") -> float:
        return float(self._call("GET", "/api/v1/buying-power", params={"currency": currency}, account=True)["cashBuyingPower"])

    def holdings(self, symbol: str | None = None) -> dict:
        return self._call("GET", "/api/v1/holdings", params={"symbol": symbol} if symbol else None, account=True)

    def sellable_quantity(self, symbol: str) -> int:
        return int(float(self._call("GET", "/api/v1/sellable-quantity", params={"symbol": symbol}, account=True)["sellableQuantity"]))

    # -- 시세 ------------------------------------------------------------------------------
    def prices(self, symbols: list[str]) -> dict[str, float]:
        rows = self._call("GET", "/api/v1/prices", params={"symbols": ",".join(symbols)})
        rows = rows if isinstance(rows, list) else rows.get("prices", rows.get("items", []))
        return {r["symbol"]: float(r["lastPrice"]) for r in rows}

    def securities_lending(self, symbol: str, count: int = 10):
        return self._call("GET", f"/api/v1/stocks/{symbol}/securities-lending", params={"count": count})

    # -- 주문 ------------------------------------------------------------------------------
    def create_order(self, *, symbol: str, side: str, quantity: int, price: int, client_order_id: str,
                     order_type: str = "LIMIT", tif: str = "DAY") -> dict:
        body = {"clientOrderId": client_order_id, "symbol": symbol, "side": side, "orderType": order_type,
                "timeInForce": tif, "quantity": str(int(quantity))}
        if order_type == "LIMIT":
            body["price"] = str(int(price))
        return self._call("POST", "/api/v1/orders", json=body, account=True)

    def order(self, order_id: str) -> dict:
        return self._call("GET", f"/api/v1/orders/{order_id}", account=True)

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        r = self._call("GET", "/api/v1/orders", params={"status": "OPEN", **({"symbol": symbol} if symbol else {})}, account=True)
        return r.get("orders", []) if isinstance(r, dict) else r

    def closed_orders(self, symbol: str, day: str) -> list[dict]:
        """해당 일자(YYYY-MM-DD, KST)에 주문된 종료 주문(체결/취소/거부). 전송 여부 복구 시 매칭용."""
        r = self._call("GET", "/api/v1/orders", params={"status": "CLOSED", "symbol": symbol, "from": day, "to": day, "limit": 100}, account=True)
        return r.get("orders", []) if isinstance(r, dict) else r

    def cancel_order(self, order_id: str) -> dict:
        return self._call("POST", f"/api/v1/orders/{order_id}/cancel", json={}, account=True)

    def create_oco(self, *, symbol: str, quantity: int, target_trigger: int, target_price: int,
                   stop_trigger: int, stop_price: int, expire: date, client_order_id: str) -> dict:
        """익절(first, 현재가 위) / 손절(second, 현재가 아래) 동시 감시. 둘 다 SELL LIMIT. KR 은 정규장에서만 발동."""
        body = {"clientOrderId": client_order_id, "symbol": symbol, "type": "OCO", "quantity": str(int(quantity)),
                "orderType": "LIMIT", "expireDate": expire.isoformat(),
                "first": {"orderSide": "SELL", "triggerPrice": str(int(target_trigger)), "orderPrice": str(int(target_price))},
                "second": {"orderSide": "SELL", "triggerPrice": str(int(stop_trigger)), "orderPrice": str(int(stop_price))}}
        return self._call("POST", "/api/v1/conditional-orders", json=body, account=True)

    def conditional_order(self, conditional_order_id: str) -> dict:
        return self._call("GET", f"/api/v1/conditional-orders/{conditional_order_id}", account=True)

    def cancel_conditional(self, conditional_order_id: str) -> None:
        self._call("DELETE", f"/api/v1/conditional-orders/{conditional_order_id}", account=True)

    def sectors(self) -> list[dict]:
        return self._call("GET", "/api/v1/sectors")["sectors"]

    def sector_performance(self, sector_id: str, market: str = "KR") -> list[dict]:
        r = self._call("GET", f"/api/v1/sectors/{sector_id}", params={"marketCountry": market})
        return (r.get("performance") or {}).get("changeRates", [])

    def sector_stocks(self, sector_id: str) -> list[str]:
        r = self._call("GET", f"/api/v1/sectors/{sector_id}/stocks", params={"marketCountry": "KR"})
        return [x["symbol"] for x in r["stocks"]]

    def kr_market_open_today(self) -> dict:
        return self._call("GET", "/api/v1/market-calendar/KR")
