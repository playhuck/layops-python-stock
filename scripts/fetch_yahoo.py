"""yfinance 로 시세(.KS/.KQ)와 ^VIX 를 data/ 에 저장. 수급(개인/기관/외국인)은 제공되지 않는다.
사용: python scripts/fetch_yahoo.py [--kosdaq 코드 ...]"""
import sys
from pathlib import Path
import yaml, yfinance as yf

root = Path(__file__).resolve().parent.parent
cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
kosdaq = set(sys.argv[2:]) if len(sys.argv) > 2 and sys.argv[1] == "--kosdaq" else set()
for it in cfg["watchlist"]:
    sym = it["code"] + (".KQ" if it["code"] in kosdaq else ".KS")
    df = yf.Ticker(sym).history(period="2y", auto_adjust=False)
    if df.empty:
        print(f"[실패] {sym}: 데이터 없음"); continue
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_localize(None); df.index.name = "date"
    df.to_csv(root / "data" / "prices" / f"{it['code']}.csv"); print(f"[ok] {sym} {len(df)}행")
v = yf.Ticker("^VIX").history(period="1mo")
if not v.empty:
    v = v.rename(columns=str.lower)[["close"]]; v.index = v.index.tz_localize(None); v.index.name = "date"
    v.to_csv(root / "data" / "vix.csv"); print("[ok] ^VIX")
