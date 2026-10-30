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


def test_incomplete_bar_is_not_stored_and_never_overwrites_a_good_close():
    # 마감 직후 Yahoo 가 당일 종가를 비워 주는 경우: 이전에는 NULL 이 정상 값을 덮어써 화면 전체가 NaN 으로 죽었다
    marketdb.upsert_prices("A", frame(3))
    bad = frame(3).assign(close=[1.0, 2.0, np.nan])
    marketdb.upsert_prices("A", pd.concat([bad, frame(1, "2026-10-08").assign(close=np.nan)]))
    df = marketdb.read_prices("A")
    assert len(df) == 3 and df["close"].tolist() == [1.0, 2.0, 3.0]            # 마지막 정상 종가(3.0) 유지, 종가 없는 새 날짜는 저장 안 됨


def test_read_prices_skips_rows_that_already_have_null_ohlc():
    marketdb.upsert_prices("A", frame(3))
    db.get().execute("INSERT INTO prices(code,date,open,high,low,close,volume) VALUES('A','2026-10-09',1,2,0.5,NULL,100)")
    db.get().execute("INSERT INTO prices(code,date,open,high,low,close,volume) VALUES('A','2026-10-12',NULL,2,0.5,1,100)")
    df = marketdb.read_prices("A")
    assert len(df) == 3 and not df.isna().any().any()


def test_vix_date_and_delete():
    marketdb.upsert_vix(pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"]), "close": [15.0, 17.5, 19.0]}))
    assert marketdb.latest_vix_date().isoformat() == "2026-10-05"
    marketdb.delete_vix([pd.Timestamp("2026-10-05").date(), pd.Timestamp("2026-10-03").date()])      # 없는 날짜가 섞여 있어도 된다
    assert marketdb.latest_vix() == 17.5 and marketdb.read_vix_dates() == [pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-02").date()]
    marketdb.delete_vix([])
    assert len(marketdb.read_vix_dates()) == 2
