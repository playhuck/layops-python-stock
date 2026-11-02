"""한국 일봉은 장 마감(KST 15:40) 전의 오늘 봉(장중 값)을 저장·판단에 쓰지 않는다."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from pgtest import clean

from stockwatch import collector, db, marketdb
from stockwatch.providers import DbProvider
from stockwatch.volume_profile import KST

TODAY = "2026-11-03"          # 화요일


def bars(last=TODAY, n=4):
    idx = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame({"open": 100.0, "high": 102.0, "low": 99.0, "close": np.arange(n) + 100.0, "volume": 1000.0}, index=idx)


def at(h, m, day=TODAY):
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=KST)


def raw_frame(df):                                       # collector._yahoo 가 돌려주는 모양(날짜가 컬럼)
    return df.rename_axis("date").reset_index()


def test_kr_prices_drops_todays_intraday_bar_until_the_close():
    df = raw_frame(bars())
    assert len(collector.kr_prices(df, at(11, 0))) == 3 and collector.kr_prices(df, at(11, 0))["date"].iloc[-1] == pd.Timestamp("2026-11-02")
    assert len(collector.kr_prices(df, at(15, 39))) == 3
    assert len(collector.kr_prices(df, at(15, 40))) == 4                       # 마감 이후에는 오늘 봉이 완성
    assert len(collector.kr_prices(df, at(1, 0, "2026-11-04"))) == 4           # 다음 날 새벽 자동 갱신: 어제까지 모두 완성
    assert list(collector.kr_prices(df, at(11, 0)).columns) == ["date", "open", "high", "low", "close", "volume"]


def test_refresh_kr_prices_saves_only_completed_bars(monkeypatch):
    saved = {}
    monkeypatch.setattr(collector, "_yahoo", lambda sym, period: raw_frame(bars()))
    monkeypatch.setattr(collector.marketdb, "upsert_prices", lambda code, df: saved.update(code=code, df=df))
    collector.refresh_kr_prices("005930", ".KS", now=at(10, 0))
    assert saved["code"] == "005930" and len(saved["df"]) == 3
    collector.refresh_kr_prices("005930", ".KS", now=at(16, 0))
    assert len(saved["df"]) == 4


class DbSetup:
    def setup_method(self):
        db.use(clean())

    def teardown_method(self):
        db.use(None)


class TestProviderFiltersStoredIntradayRow(DbSetup):
    def test_reading_hides_a_stored_intraday_bar_but_shows_it_after_the_close(self):
        marketdb.upsert_prices("A", bars())                                      # 수정 전에 장중에 저장돼 있던 오늘 행
        p = DbProvider()
        p._now = lambda: at(11, 0)
        assert len(p.prices("A")) == 3 and p.prices("A").index[-1] == pd.Timestamp("2026-11-02")
        p._now = lambda: at(16, 0)
        assert len(p.prices("A")) == 4

    def test_missing_code_still_raises(self):
        with pytest.raises(FileNotFoundError):
            DbProvider().prices("NOPE")

    def test_only_an_intraday_row_counts_as_no_data(self):
        marketdb.upsert_prices("B", bars(n=1))
        p = DbProvider(); p._now = lambda: at(11, 0)
        with pytest.raises(FileNotFoundError):
            p.prices("B")
