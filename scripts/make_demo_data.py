"""합성(가짜) 데이터 생성: 시스템 동작 확인용. 실제 시세가 아니다."""
from pathlib import Path
import numpy as np, pandas as pd

root = Path(__file__).resolve().parent.parent / "data"
rng = np.random.default_rng(42)
idx = pd.bdate_range(end="2026-10-28", periods=260)
for code, drift in (("005930", 0.0012), ("000660", 0.0006)):
    ret = rng.normal(drift, 0.015, len(idx)); ret[-5:] = -0.006
    close = 70000 * np.exp(np.cumsum(ret))
    df = pd.DataFrame({"date": idx, "open": close * (1 + rng.normal(0, .004, len(idx))),
                       "close": close})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.008
    df["low"] = df[["open", "close"]].min(axis=1) * 0.992
    df["volume"] = rng.integers(8_000_000, 20_000_000, len(idx))
    df[["date", "open", "high", "low", "close", "volume"]].round(0).to_csv(root / "prices" / f"{code}.csv", index=False)
    fl = pd.DataFrame({"date": idx[-30:], "individual": rng.integers(-3e5, 3e5, 30),
                       "institution": rng.integers(-2e5, 3e5, 30), "foreign": rng.integers(-2e5, 4e5, 30)})
    fl.to_csv(root / "flows" / f"{code}.csv", index=False)
pd.DataFrame({"date": idx[-5:], "close": [17.2, 17.8, 18.1, 17.5, 17.9]}).to_csv(root / "vix.csv", index=False)
print("demo data written")
