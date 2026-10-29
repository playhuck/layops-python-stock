import numpy as np
import pandas as pd
from pgtest import clean

from stockwatch import db, marketdb


def setup_function():
    db.use(clean())


def teardown_function():
    db.use(None)


def frame(n=5, start="2026-10-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": np.arange(n) + 1.0, "volume": 100.0}, index=idx)


def test_prices_roundtrip_and_upsert_accumulates_history():
    marketdb.upsert_prices("A", frame(5))
    marketdb.upsert_prices("A", frame(5).assign(close=99.0))                     # 같은 날짜 재수집 → 덮어쓰기(중복 행 없음)
    marketdb.upsert_prices("A", frame(3, "2026-10-08"))                          # 새 날짜 → 이력 누적
    df = marketdb.read_prices("A")
    assert len(df) == 8 and df["close"].iloc[0] == 99.0 and df.index.is_monotonic_increasing
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_flows_foreign_column_mapped_back():
    f = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]), "institution": [1, 2], "other_corp": [0, 0],
                      "individual": [-3, -4], "foreign": [2, 2]})
    marketdb.upsert_flows("A", f)
    out = marketdb.read_flows("A")
    assert out["foreign"].tolist() == [2.0, 2.0] and out["individual"].tolist() == [-3.0, -4.0]
    assert marketdb.read_flows("ZZZ") is None


def test_dataset_nan_becomes_null_and_roundtrips():
    df = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]), "per": [10.5, np.nan], "pbr": [1, 2]})
    marketdb.save_dataset("fundamental", "A", df)
    out = marketdb.read_dataset("fundamental", "A")
    assert out["pbr"].tolist() == [1, 2] and np.isnan(out["per"].iloc[1]) and out["per"].iloc[0] == 10.5
    marketdb.save_dataset("fundamental", "A", df.assign(pbr=[7, 8]))             # upsert
    assert marketdb.read_dataset("fundamental", "A")["pbr"].tolist() == [7, 8]


def test_vix_latest():
    assert marketdb.latest_vix() is None
    marketdb.upsert_vix(pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]), "close": [15.0, 17.5]}))
    assert marketdb.latest_vix() == 17.5


def test_all_null_column_reads_as_numeric_nan_not_none():
    df = pd.DataFrame({"date": pd.to_datetime(["2026-10-29"]), "per": [np.nan], "pbr": [np.nan], "eps": [6605]})
    marketdb.save_dataset("fundamental", "A", df)
    out = marketdb.read_dataset("fundamental", "A")
    assert out["per"].dtype.kind == "f" and np.isnan(out["per"].iloc[0])        # None(object) 이면 포맷 시 TypeError


def test_summary_survives_missing_todays_per_and_uses_latest_valid_row():
    from stockwatch import cli
    d = pd.DataFrame({"date": pd.to_datetime(["2026-10-28", "2026-10-29"]), "per": [35.0, np.nan], "pbr": [3.6, np.nan],
                      "eps": [6605, np.nan], "dvd_yld": [0.7, np.nan]})
    marketdb.save_dataset("fundamental", "A", d)

    class P:
        def table(self, name, code): return marketdb.read_dataset(name, code)

    line = next(x for x in cli.summarize_extra(P(), "A", pd.DataFrame({"close": [1.0] * 30})) if x.startswith("PER"))
    assert "PER 35.0" in line and "PBR 3.60" in line
