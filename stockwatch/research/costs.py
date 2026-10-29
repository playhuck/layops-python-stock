"""백테스트 거래 비용 모델: 수수료 + 슬리피지 + (시장·양도일별) 세금."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

SCHEDULE_PATH = Path(__file__).with_name("tax_schedule.yaml")


class CostScheduleError(ValueError):
    pass


def load_schedule(path: Path = SCHEDULE_PATH) -> tuple:
    """[(적용 시작일, {시장: 세금 합계})] 오름차순. 시장 키: STK(코스피) / KSQ(코스닥)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["periods"]
    out = []
    for p in raw:
        start = datetime.strptime(str(p["from"]), "%Y-%m-%d").date()
        out.append((start, {m: float(p[m]["transaction"]) + float(p[m]["rural"]) for m in ("STK", "KSQ")}))
    return tuple(sorted(out, key=lambda x: x[0]))


@dataclass(frozen=True)
class CostModel:
    commission: float = 0.00015       # 토스 KRX 편도 수수료 (/api/v1/commissions 로 확인한 값)
    slippage_buy: float = 0.001
    slippage_sell: float = 0.001
    schedule: tuple = field(default_factory=load_schedule)

    @classmethod
    def from_config(cls, cfg: dict) -> "CostModel":
        c = cfg.get("backtest_costs", {}) or {}
        return cls(commission=float(c.get("commission_krx", cls.commission)), slippage_buy=float(c.get("slippage_buy", cls.slippage_buy)),
                   slippage_sell=float(c.get("slippage_sell", cls.slippage_sell)))

    def tax_rate(self, settle: date, market: str) -> float:
        if market not in ("STK", "KSQ"):
            raise CostScheduleError(f"지원하지 않는 시장: {market}")
        if settle < self.schedule[0][0]:
            raise CostScheduleError(f"{settle} 은(는) 세율표가 덮는 첫 날짜({self.schedule[0][0]}) 이전입니다. tax_schedule.yaml 에 연도를 추가하세요")
        rate = None
        for start, rates in self.schedule:
            if start <= settle:
                rate = rates[market]
        return rate

    def buy_factor(self) -> float:
        return (1 + self.slippage_buy) * (1 + self.commission)      # fill_buy 와 같은 구조(체결가에 슬리피지, 그 위에 수수료)

    def fill_buy(self, open_px: float) -> float:
        """매수 체결가 = 시가 + 슬리피지. 손절·목표가는 이 값 기준(실전 보호주문도 실제 체결가 기준). 수수료는 현금 차감에만 반영한다."""
        return open_px * (1 + self.slippage_buy)

    def sell_factor(self, settle: date, market: str) -> float:
        return 1 - self.commission - self.slippage_sell - self.tax_rate(settle, market)

    def round_trip(self, settle: date, market: str) -> float:
        return (self.buy_factor() / self.sell_factor(settle, market)) - 1


def settlement_date(dates: pd.DatetimeIndex, idx: int, lag: int = 2) -> date:
    """체결일(dates[idx])의 결제일 = 그 종목 거래일 달력으로 lag 거래일 뒤. 세금은 양도일(결제일) 기준이므로 연말 경계에서 체결일과 세율이 달라질 수 있다.
    달력 끝(미래)을 넘으면 평일 기준으로 근사한다."""
    j = idx + lag
    if j < len(dates):
        return dates[j].date()
    cur, need = dates[-1].date(), j - (len(dates) - 1)
    while need > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            need -= 1
    return cur
