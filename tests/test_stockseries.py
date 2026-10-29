import numpy as np
import pandas as pd

from stockwatch import stockseries


class P:
    def __init__(self):
        idx = pd.to_datetime(["2026-10-27", "2026-10-28", "2026-10-29"])
        self.f = pd.DataFrame({"institution": [1, 2, 3], "foreign": [4, 5, np.nan], "individual": [-5, -7, 1], "other_corp": [0, 0, 0]}, index=idx)
        self.s = pd.DataFrame({"cvsrtsell_trdvol": [10, 20, 0], "trdval_wt": [1.0, 2.0, 0.0], "cvsrtsell_trdval": [5, 6, 0]}, index=idx)

    def flows(self, code): return self.f

    def table(self, name, code):
        return {"short_trading": self.s, "foreign_own": None, "short_balance": None}[name]


def test_build_shapes_nan_to_null_and_trailing_unpublished_short_dropped():
    out = stockseries.build(P(), "005930", 60)
    assert [r["date"] for r in out["flows"]] == ["2026-10-27", "2026-10-28", "2026-10-29"]
    assert out["flows"][2]["foreign"] is None                                    # NaN 은 null
    assert [r["date"] for r in out["short"]] == ["2026-10-27", "2026-10-28"]     # 당일 미공개(0) 제외
    assert out["foreign_own"] == [] and out["short_balance"] == []
    assert len(stockseries.build(P(), "005930", 5)["flows"]) == 3
