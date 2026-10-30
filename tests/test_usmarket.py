import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from pgtest import clean

from stockwatch import db, marketdb, usmarket

ET = ZoneInfo("America/New_York")


def setup_function():
    db.use(clean())


def teardown_function():
    db.use(None)


def bars(n=130, end="2026-10-29", close0=10.0, step=0.05, volume=2_000_000.0):
    idx = pd.bdate_range(end=end, periods=n)
    c = close0 + np.arange(n) * step
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": volume}, index=idx)


def test_incomplete_us_bar_is_dropped_until_after_close():
    df = bars(5, end="2026-10-29")                                          # 마지막 봉 = 10/29(목)
    mid = datetime(2026, 10, 29, 12, 0, tzinfo=ET)                          # 장중
    after = datetime(2026, 10, 29, 16, 31, tzinfo=ET)                       # 마감 뒤
    weekend = datetime(2026, 10, 31, 9, 0, tzinfo=ET)                       # 토요일
    kst_morning = datetime(2026, 10, 30, 7, 0, tzinfo=ZoneInfo("Asia/Seoul"))   # 한국시간 7시 = 동부 10/29 18시(서머타임 아님이면 17시)
    assert len(usmarket.complete_us_bars(df, mid)) == 4 and df.index[-1] not in usmarket.complete_us_bars(df, mid).index
    assert len(usmarket.complete_us_bars(df, after)) == 5
    assert len(usmarket.complete_us_bars(df, weekend)) == 5
    assert len(usmarket.complete_us_bars(df, kst_morning)) == 5             # 07시 갱신은 직전 미국 거래일의 완성된 봉을 본다
    assert usmarket.complete_us_bars(df.iloc[:0], mid).empty


def _raw(frames: dict) -> pd.DataFrame:
    parts = {s: f.rename(columns=str.capitalize) for s, f in frames.items()}
    return pd.concat(parts, axis=1)                                          # yfinance download(group_by="ticker") 와 같은 MultiIndex 컬럼


def test_fetch_bars_skips_nan_bars_and_isolates_failures(monkeypatch):
    good, bad = bars(30), bars(30)
    bad.iloc[-1, bad.columns.get_loc("close")] = np.nan                      # 마감 직후 종가가 빈 봉
    raw = _raw({"GOOD": good, "BAD": bad})
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw)
    out, err = usmarket.fetch_bars(["GOOD", "BAD", "MISSING"], now=datetime(2026, 10, 30, 12, 0, tzinfo=ET))
    assert len(out["GOOD"]) == 30 and out["GOOD"].index.name == "date"
    assert len(out["BAD"]) == 29 and out["BAD"]["close"].notna().all()       # 불완전 봉만 빠지고 나머지는 저장 대상
    assert err == {"MISSING": "데이터 없음"}


def test_fetch_bars_survives_download_exception(monkeypatch):
    import yfinance as yf

    def boom(*a, **k):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(yf, "download", boom)
    out, err = usmarket.fetch_bars(["A", "B"])
    assert out == {} and set(err) == {"A", "B"} and "rate limited" in err["A"]


def test_stock_metrics_values_and_flags():
    df = bars(130, close0=10.0, step=0.1, volume=100_000.0)                  # 거래대금 약 $1.9M → 유동성 낮음
    df.iloc[-1, df.columns.get_loc("open")] = df["close"].iloc[-2] * 1.15    # 마지막 날 +15% 갭
    df.iloc[-1, df.columns.get_loc("volume")] = 300_000.0
    meta = {"market_cap": 5e8, "short_pct_float": 25.0, "beta": 2.0, "sector": "Technology", "industry": "x", "next_earnings": date(2026, 11, 3)}
    nasdaq = bars(130, close0=100.0, step=0.0)                               # 평평한 벤치마크 → RS = 종목 수익률
    m = usmarket.stock_metrics("TST", "Test", df, {"nasdaq": nasdaq, "small": None}, meta, today=date(2026, 10, 30))
    assert m["vol_ratio"] == pytest.approx(3.0)
    assert m["max_gap_60d"] == pytest.approx(15.0, abs=0.2)
    assert m["days_to_earnings"] == 4 and m["next_earnings"] == "2026-11-03"
    assert m["rs20_nasdaq"] == pytest.approx(m["chg_20d"]) and m["rs20_small"] is None   # 벤치마크가 없으면 None (0 이 아님)
    assert m["above_ma20"] is True and m["from_high52_pct"] <= 0
    joined = " | ".join(m["flags"])
    assert "실적 발표 D-4" in joined and "유동성 낮음" in joined and "갭 +" in joined and "공매도 비중 높음" in joined


def test_stock_metrics_short_history_and_stale_flags():
    m = usmarket.stock_metrics("NEW", "New", bars(40, end="2026-10-20"), {"nasdaq": None, "small": None}, None, today=date(2026, 10, 30))
    joined = " | ".join(m["flags"])
    assert "40일치뿐" in joined and "10일 전 데이터" in joined and m["next_earnings"] is None


def test_watchlist_add_remove_limits_and_cleanup(monkeypatch):
    usmarket.add_watch("soun", " SoundHound ")
    assert usmarket.watch_symbols() == ["SOUN"]
    with pytest.raises(ValueError):
        usmarket.add_watch("SOUN", "dup")
    with pytest.raises(ValueError):
        usmarket.add_watch("TOOLONGX", "x")
    with pytest.raises(ValueError):
        usmarket.add_watch("005930", "kr code")                              # 한국 코드는 티커가 아니다
    monkeypatch.setattr(usmarket, "MAX_WATCH", 1)
    with pytest.raises(ValueError):
        usmarket.add_watch("IONQ", "x")
    marketdb.upsert_prices("SOUN", bars(30)); marketdb.upsert_prices("^IXIC", bars(30))
    usmarket.save_meta("SOUN", {"name": None, "market_cap": 1.0, "avg_volume": 2.0, "float_shares": 3.0, "short_pct_float": 4.0, "beta": 5.0,
                                "sector": "Tech", "industry": "SW", "next_earnings": date(2026, 11, 5)})
    assert usmarket.read_meta("SOUN")["next_earnings"] == date(2026, 11, 5)
    usmarket.remove_watch("SOUN")
    assert marketdb.read_prices("SOUN").empty and usmarket.read_meta("SOUN") is None
    assert not marketdb.read_prices("^IXIC").empty                           # 시장 지표 시세는 남는다
    with pytest.raises(KeyError):
        usmarket.remove_watch("SOUN")


def test_add_and_collect_rejects_unknown_ticker_and_stores_prices(monkeypatch):
    monkeypatch.setattr(usmarket, "_collect_meta", lambda s: None)
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({"GOOD": bars(30)}, {"NOPE": "데이터 없음"}))
    with pytest.raises(ValueError, match="가져오지 못했어요"):
        usmarket.add_and_collect("NOPE")
    assert usmarket.watch_symbols() == []                                    # 확인 실패 시 목록에 넣지 않는다
    usmarket.add_and_collect("GOOD", "Good Co")
    assert usmarket.watch_symbols() == ["GOOD"] and len(marketdb.read_prices("GOOD")) == 30


def test_overviews_from_db_and_pending_symbol():
    marketdb.upsert_prices("^IXIC", bars(130, close0=100.0, step=0.5)); marketdb.upsert_prices("IWM", bars(130, close0=50.0, step=0.1))
    marketdb.upsert_prices("AAA", bars(130))
    usmarket.add_watch("AAA", "A"); usmarket.add_watch("BBB", "B")           # BBB 는 시세가 아직 없다
    mk = {r["symbol"]: r for r in usmarket.market_overview()}
    assert mk["^IXIC"]["trend"] == "상승 배열" and mk["^IXIC"]["chg_1d"] > 0 and "^NDX" not in mk
    rows = {r["symbol"]: r for r in usmarket.stocks_overview()}
    assert rows["AAA"]["rs20_small"] is not None and rows["BBB"]["pending"] is True


def test_refresh_us_stores_only_complete_bars_and_reports_failures(monkeypatch):
    usmarket.add_watch("AAA", "A")
    complete = bars(30)
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({"AAA": complete, "^IXIC": complete}, {"^NDX": "데이터 없음"}))
    monkeypatch.setattr(usmarket, "fetch_meta", lambda s: {"name": "A Inc", "market_cap": 1e9, "avg_volume": 1e6, "float_shares": 1e8, "short_pct_float": 3.0,
                                                           "beta": 1.5, "sector": "Tech", "industry": "SW", "next_earnings": None})
    assert usmarket.refresh_us("test") is True
    assert len(marketdb.read_prices("AAA")) == 30 and usmarket.read_meta("AAA")["sector"] == "Tech"
    failed = [s for s in usmarket.status["steps"] if not s["ok"]]
    assert [s["name"] for s in failed] == ["시세 ^NDX"] and usmarket.status["running"] is False


def test_refresh_us_meta_failure_keeps_previous_meta(monkeypatch):
    usmarket.add_watch("AAA", "A")
    usmarket.save_meta("AAA", {"name": None, "market_cap": 7.0, "avg_volume": None, "float_shares": None, "short_pct_float": None, "beta": None,
                               "sector": "Old", "industry": None, "next_earnings": None})
    db.get().execute("UPDATE us_meta SET updated_at = now() - interval '30 hours' WHERE symbol='AAA'")   # 캐시 기한이 지나 다시 받으려는 상황
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({}, {}))

    def boom(s):
        raise RuntimeError("429")
    monkeypatch.setattr(usmarket, "fetch_meta", boom)
    usmarket.refresh_us("test")
    assert usmarket.read_meta("AAA")["sector"] == "Old"                      # 조회 실패가 이전 값을 지우지 않는다
    assert any(s["name"] == "종목정보 AAA" and not s["ok"] for s in usmarket.status["steps"])


def test_us_api_strict_json_guard_and_flow(monkeypatch):
    from stockwatch.web import app
    c = app.test_client()
    strict = lambda r: json.loads(r.data, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    monkeypatch.setattr(usmarket, "live_snapshot", lambda ttl=60.0: [])
    monkeypatch.setattr(usmarket, "_collect_meta", lambda s: None)
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({"AAA": bars(130)}, {}))
    assert c.post("/api/us/watchlist", json={"symbol": "AAA"}).status_code == 403        # 상태 변경은 X-Stockwatch 헤더 필수
    h = {"X-Stockwatch": "1"}
    assert c.post("/api/us/watchlist", json={"symbol": "AAA", "name": "Aaa"}, headers=h).status_code == 200
    assert c.post("/api/us/watchlist", json={"symbol": "AAA"}, headers=h).status_code == 400   # 중복
    assert c.post("/api/us/watchlist", json={"symbol": "zz9"}, headers=h).status_code == 400   # 형식 오류
    marketdb.upsert_prices("^IXIC", bars(130))
    body = strict(c.get("/api/us/stocks"))
    assert body["stocks"][0]["symbol"] == "AAA" and body["max"] == usmarket.MAX_WATCH
    assert strict(c.get("/api/us/overview"))["market"][0]["symbol"] == "^IXIC"
    assert len(strict(c.get("/api/us/series/AAA"))) == 120 and c.get("/api/us/series/ZZZ").status_code == 404
    assert c.delete("/api/us/watchlist/AAA").status_code == 403
    assert c.delete("/api/us/watchlist/AAA", headers=h).status_code == 200 and c.delete("/api/us/watchlist/AAA", headers=h).status_code == 404


def test_us_discover_marks_added_and_handles_failure(monkeypatch):
    from stockwatch.web import app
    c = app.test_client()
    usmarket.add_watch("AAA", "A")
    monkeypatch.setattr(usmarket, "discover", lambda *a, **k: [{"symbol": "AAA", "name": "A"}, {"symbol": "BBB", "name": "B"}])
    assert [(r["symbol"], r["added"]) for r in c.get("/api/us/discover").get_json()] == [("AAA", True), ("BBB", False)]

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(usmarket, "discover", boom)
    assert c.get("/api/us/discover").status_code == 502


# -- 선물 기준값: 가장 최근 미 동부 16:00 시점 ------------------------------------------------------
def m30(*pairs):
    return pd.DataFrame({"Close": [v for _, v in pairs]}, index=pd.DatetimeIndex([pd.Timestamp(t, tz=ET) for t, _ in pairs]))


def test_futures_change_uses_latest_1600_et_not_yahoo_previous_close():
    h = m30(("2026-10-28 15:30", 29979.5), ("2026-10-29 15:30", 29566.0), ("2026-10-29 16:00", 29237.0), ("2026-10-29 23:30", 28947.0), ("2026-10-30 04:30", 28928.75))
    f = usmarket.futures_change(h, datetime(2026, 10, 30, 4, 45, tzinfo=ET))
    assert f["base"] == 29566.0 and f["last"] == 28928.75 and f["base_at"] == "2026-10-29 16:00 ET"
    assert f["chg"] == pytest.approx(-2.155, abs=0.01)                      # 야후 previous_close(28947)를 썼을 때의 −0.06% 가 아니다


def test_futures_base_is_previous_day_until_1600_and_today_after():
    h = m30(("2026-10-28 15:30", 100.0), ("2026-10-29 11:30", 101.0), ("2026-10-29 15:30", 105.0), ("2026-10-29 16:00", 99.0))
    before = usmarket.futures_change(h.iloc[:2], datetime(2026, 10, 29, 12, 0, tzinfo=ET))
    mid = usmarket.futures_change(h.iloc[:3], datetime(2026, 10, 29, 15, 45, tzinfo=ET))      # 15:30 봉은 16:00 에 끝난다 → 아직 기준이 아님
    after = usmarket.futures_change(h, datetime(2026, 10, 29, 16, 10, tzinfo=ET))
    assert before["base"] == 100.0 and mid["base"] == 100.0 and after["base"] == 105.0


def test_futures_base_across_dst_and_weekend_and_standard_time():
    est = m30(("2026-12-10 15:30", 200.0), ("2026-12-11 05:00", 190.0))                       # 표준시(EST)
    assert usmarket.futures_change(est, datetime(2026, 12, 11, 5, 10, tzinfo=ET))["base"] == 200.0
    wk = m30(("2026-10-30 15:30", 300.0), ("2026-10-30 17:00", 301.0), ("2026-10-31 10:00", 299.0))   # 토요일에 확인
    f = usmarket.futures_change(wk, datetime(2026, 10, 31, 10, 5, tzinfo=ET))
    assert f["base"] == 300.0 and f["base_at"] == "2026-10-30 16:00 ET"
    kst = usmarket.futures_change(est, datetime(2026, 12, 11, 19, 10, tzinfo=ZoneInfo("Asia/Seoul")))   # 시각대가 달라도 같은 순간이면 같은 결과
    assert kst["base"] == 200.0


def test_futures_hidden_when_reference_cannot_be_found():
    assert usmarket.futures_change(m30(("2026-10-29 11:30", 1.0), ("2026-10-29 12:00", 2.0)), datetime(2026, 10, 29, 12, 5, tzinfo=ET)) is None   # 16:00 시점 봉이 없다
    old = m30(("2026-10-20 15:30", 1.0), ("2026-10-29 12:00", 2.0))
    assert usmarket.futures_change(old, datetime(2026, 10, 29, 12, 5, tzinfo=ET)) is None          # 기준이 5일보다 오래됐다
    assert usmarket.futures_change(m30(), datetime(2026, 10, 29, 12, 5, tzinfo=ET)) is None


# -- VIX: 완성된 일봉만 저장하고, 이번 응답에서 장중으로 확인된 날짜만 지운다 -----------------------------------
def vixdf(rows):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "close": [c for _, c in rows]})


def test_persist_vix_drops_intraday_row_found_in_the_response():
    marketdb.upsert_vix(vixdf([("2026-10-28", 16.91), ("2026-10-30", 18.45)]))                # 10/30 은 이전에 장중 값으로 저장된 행
    now = datetime(2026, 10, 30, 7, 0, tzinfo=ZoneInfo("Asia/Seoul"))                         # = 동부 10/29 18시
    usmarket.persist_vix(vixdf([("2026-10-28", 16.91), ("2026-10-29", 17.20), ("2026-10-30", 18.54)]), now)
    assert marketdb.latest_vix() == 17.20 and marketdb.latest_vix_date() == date(2026, 10, 29)
    assert marketdb.read_vix_dates() == [date(2026, 10, 28), date(2026, 10, 29)]              # 10/30 행이 사라졌다


@pytest.mark.parametrize("day,tz_note", [("2026-10-29", "EDT"), ("2026-12-09", "EST")])
def test_persist_vix_boundary_1629_vs_1630_et(day, tz_note):
    d = date.fromisoformat(day)
    before = datetime(d.year, d.month, d.day, 16, 29, tzinfo=ET); after = datetime(d.year, d.month, d.day, 16, 30, tzinfo=ET)
    usmarket.persist_vix(vixdf([(day, 20.0)]), before)
    assert marketdb.latest_vix() is None                                                      # 16:29 에는 당일 봉을 저장하지 않는다
    usmarket.persist_vix(vixdf([(day, 20.0)]), after)
    assert marketdb.latest_vix() == 20.0 and marketdb.latest_vix_date() == d


def test_persist_vix_never_deletes_dates_missing_from_the_response():
    marketdb.upsert_vix(vixdf([("2026-10-27", 17.5), ("2026-10-28", 16.91)]))
    now = datetime(2026, 10, 30, 7, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    usmarket.persist_vix(vixdf([("2026-10-27", 17.5)]), now)                                  # 야후가 오래된 데이터까지만 줬다
    usmarket.persist_vix(vixdf([]), now)                                                      # 빈 응답
    assert marketdb.read_vix_dates() == [date(2026, 10, 27), date(2026, 10, 28)]


def test_korean_collector_vix_step_uses_the_same_completed_bar_rule(monkeypatch):
    from stockwatch import collector
    monkeypatch.setattr(collector, "_yahoo", lambda sym, period: vixdf([("2026-10-29", 17.2), ("2099-01-01", 99.0)]))   # 미래 날짜 = 아직 열리지 않은 세션
    collector.refresh_vix()
    assert marketdb.latest_vix() == 17.2


# -- 메타 캐시 / 입력 범위 ----------------------------------------------------------------------
META = {"name": "N", "market_cap": 1e9, "avg_volume": 1e6, "float_shares": 1e8, "short_pct_float": 3.0, "beta": 1.5, "sector": "Tech", "industry": "SW", "next_earnings": None}


def test_refresh_us_skips_fresh_meta_but_fetches_missing_and_stale(monkeypatch):
    for s in ("AAA", "BBB", "CCC"):
        usmarket.add_watch(s, s)
    usmarket.save_meta("AAA", META); usmarket.save_meta("BBB", META)
    db.get().execute("UPDATE us_meta SET updated_at = now() - interval '30 hours' WHERE symbol='BBB'")
    called = []
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({}, {}))
    monkeypatch.setattr(usmarket, "fetch_meta", lambda s: called.append(s) or META)
    usmarket.refresh_us("test")
    assert sorted(called) == ["BBB", "CCC"]                                                   # 20시간 이내인 AAA 는 호출하지 않는다
    assert any(s["name"] == "종목정보 AAA" and s["ok"] for s in usmarket.status["steps"])


def test_stock_metrics_report_meta_updated_at():
    usmarket.add_watch("AAA", "A"); marketdb.upsert_prices("AAA", bars(130)); usmarket.save_meta("AAA", META)
    row = usmarket.stocks_overview()[0]
    assert row["meta_updated_at"] and row["meta_updated_at"].startswith(str(datetime.now().year))


def test_download_window_is_two_years_and_inputs_ignore_rows_beyond_400(monkeypatch):
    import yfinance as yf
    seen = {}
    monkeypatch.setattr(yf, "download", lambda *a, **k: seen.update(k) or _raw({"A": bars(30)}))
    usmarket.fetch_bars(["A"], now=datetime(2026, 10, 30, 12, 0, tzinfo=ET))
    assert seen["period"] == "2y" and usmarket.INPUT_ROWS == 400
    clean = bars(400)
    old = bars(600)
    old.iloc[:200] = old.iloc[:200] * 10                                                    # 분할 전 스케일이 남은 오래된 행 (수집 구간 밖)
    old.iloc[200:] = clean.to_numpy()
    usmarket.add_watch("AAA", "A"); usmarket.add_watch("BBB", "B")
    marketdb.upsert_prices("AAA", clean); marketdb.upsert_prices("BBB", old)
    rows = {r["symbol"]: r for r in usmarket.stocks_overview()}
    assert len(usmarket._read("BBB")) == usmarket.INPUT_ROWS == len(usmarket._read("AAA"))        # 지표 입력은 최근 400행으로 잘린다 (RSI/ATR 은 오래된 행의 영향이 거의 없어 값 비교만으로는 못 잡는다)
    for k in ("rsi", "atr_pct", "from_high52_pct", "from_low52_pct", "chg_60d", "ma20_slope", "max_gap_60d"):
        assert rows["AAA"][k] == pytest.approx(rows["BBB"][k])                              # 오래된 스케일이 지표에 새지 않는다
    assert len(usmarket.series("BBB", 120)) == 120 and usmarket.series("BBB", 120)[0]["close"] < 100
