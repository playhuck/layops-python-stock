import numpy as np
import pandas as pd

from stockwatch import marketflow


class P:
    def __init__(self):
        idx = pd.to_datetime(["2026-10-27", "2026-10-28"])
        self.mf = pd.DataFrame({"individual_val": [-1e10, 2e10], "institution_val": [5e9, np.nan], "other_corp_val": [1e8, 2e8],
                                "foreign_reg_val": [4e9, -3e10], "foreign_etc_val": [1e9, 5e9], "pension_val": [2e9, 3e9]}, index=idx)
        self.ix = pd.DataFrame({"clsprc_idx": [6000.0, 6054.48], "updn_rate": [-1.2, 0.7]}, index=idx[:2])

    def table(self, name, code):
        return {"market_flow": self.mf if code == "STK" else None, "index": self.ix}[name]


def test_daily_converts_to_eok_merges_foreign_and_index():
    out = marketflow.daily(P(), 20)
    assert [m["id"] for m in out["markets"]] == ["STK"]                         # 데이터 없는 코스닥은 제외
    r0, r1 = out["markets"][0]["rows"]
    assert r0["date"] == "2026-10-27" and r0["individual"] == -100.0 and r0["institution"] == 50.0
    assert r0["foreign"] == 50.0 and r1["foreign"] == -250.0                      # 외국인 = 외국인 + 기타외국인
    assert r1["institution"] is None                                              # 결측은 null (0 으로 위장하지 않는다)
    assert r0["parts"]["연기금"] == 20.0 and r1["index_close"] == 6054.48 and r1["index_rate"] == 0.7


def test_days_limits_rows():
    assert len(marketflow.daily(P(), 1)["markets"][0]["rows"]) == 1
