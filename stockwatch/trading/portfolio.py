"""포트폴리오 상태: 한도 검사의 단일 기준.

생성 단계(제안 만들 때)와 승인 단계(주문 보내기 직전)가 같은 계산을 쓰도록 한 곳에 모았다. 두 곳에 복제하면 다시 어긋난다.

구성 요소
- holdings : 토스에 실제 보유 중인 종목의 평가액
- reserved : 진행 중인 매수 주문(approving / ordered / needs_review)의 주문액  ← 아직 체결 반영 전이라 보유에 안 보이는 몫
- virtual  : 생성 단계에서 같은 배치에서 이미 선정한 후보(가상 보유)
- daily_used : 오늘 체결액 + 예약액 (자기 자신은 제외하고 계산해서 넘겨받는다)

종목별 노출은 max(보유 평가액, 예약액, 가상액). 합산하면 '체결됐지만 아직 동기화 안 된 주문'이 보유와 예약에 동시에 잡혀 이중 집계되므로
(매수는 미보유 종목에만 하므로) max 가 이중 집계도 누락도 없는 보수적 규칙이다.
"""
from dataclasses import dataclass, field


@dataclass
class PortfolioState:
    holdings: dict[str, float]
    reserved: dict[str, float]
    daily_used: float
    uncertain_cash: float = 0.0   # 승인 중·확인 필요 주문이 쓸 현금(토스 매수가능금액에 아직 안 빠졌을 수 있는 몫)
    virtual: dict[str, float] = field(default_factory=dict)

    def exposure(self, code: str) -> float:
        return max(self.holdings.get(code, 0.0), self.reserved.get(code, 0.0), self.virtual.get(code, 0.0))

    def codes(self) -> set[str]:
        return set(self.holdings) | set(self.reserved) | set(self.virtual)

    def real_codes(self) -> set[str]:
        return set(self.holdings) | set(self.reserved)

    def virtual_total(self) -> float:
        return float(sum(self.virtual.values()))

    def daily_total(self) -> float:
        return self.daily_used + self.virtual_total()

    def add_virtual(self, code: str, amount: float):
        self.virtual[code] = float(amount)
