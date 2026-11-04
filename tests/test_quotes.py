import numpy as np
import pandas as pd

from stockwatch import quotes


def _one(vals, tz="America/New_York"):
    idx = pd.date_range("2026-11-03 10:00", periods=len(vals), freq="1min", tz=tz)
    return pd.DataFrame({"Open": vals, "High": vals, "Low": vals, "Close": vals, "Volume": 1.0}, index=idx)


def test_multi_symbol_takes_last_non_nan_close():
    raw = pd.concat({"AAA": _one([1.0, 2.0, np.nan]), "BBB": _one([5.0, np.nan, np.nan])}, axis=1)
    q = quotes.parse_quotes(raw, ["AAA", "BBB", "CCC"])
    assert q["AAA"]["price"] == 2.0 and q["BBB"]["price"] == 5.0
    assert "CCC" not in q                       # 응답에 없는 심볼은 뺀다
    assert q["AAA"]["as_of"] < pd.Timestamp("2026-11-03 10:02", tz="America/New_York").timestamp() + 1


def test_single_symbol_flat_columns_and_empty():
    assert quotes.parse_quotes(_one([3.0, 4.0]), ["AAA"])["AAA"]["price"] == 4.0
    assert quotes.parse_quotes(pd.DataFrame(), ["AAA"]) == {}
