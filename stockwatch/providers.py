"""데이터 공급자. 분석 엔진은 이 인터페이스만 알고, 실제 수집처(CSV/증권사 API 등)는 교체 가능하다.

표준 형식
- prices: index=날짜(오름차순), 컬럼 open/high/low/close/volume
- flows : index=날짜, 컬럼 individual/institution/foreign (일별 순매수 수량, 음수=순매도)
- vix   : 최신 VIX 종가 float
"""
from pathlib import Path
from typing import Protocol

import pandas as pd


class Provider(Protocol):
    def prices(self, code: str) -> pd.DataFrame: ...
    def flows(self, code: str) -> pd.DataFrame | None: ...
    def vix(self) -> float | None: ...


class CsvProvider:
    """data/ 폴더의 CSV 를 읽는다.
    data/prices/<code>.csv : date,open,high,low,close,volume
    data/flows/<code>.csv  : date,individual,institution,foreign   (선택)
    data/vix.csv           : date,close                            (선택)
    """

    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)

    def _read(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        return df

    def prices(self, code: str) -> pd.DataFrame:
        df = self._read(self.root / "prices" / f"{code}.csv")
        if df is None:
            raise FileNotFoundError(f"시세 파일 없음: {self.root / 'prices' / f'{code}.csv'}")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def flows(self, code: str) -> pd.DataFrame | None:
        return self._read(self.root / "flows" / f"{code}.csv")

    def flow_detail(self, code: str) -> pd.DataFrame | None:
        return self._read(self.root / "flows_detail" / f"{code}.csv")

    def table(self, folder: str, code: str) -> pd.DataFrame | None:
        return self._read(self.root / folder / f"{code}.csv")

    def vix(self) -> float | None:
        df = self._read(self.root / "vix.csv")
        return None if df is None or df.empty else float(df["close"].iloc[-1])


class DbProvider:
    """PostgreSQL 에서 읽는 공급자 (CsvProvider 와 같은 인터페이스)."""

    def prices(self, code: str) -> pd.DataFrame:
        from . import marketdb
        df = marketdb.read_prices(code)
        if df.empty:
            raise FileNotFoundError(f"시세 데이터 없음: {code}")
        return df

    def flows(self, code: str) -> pd.DataFrame | None:
        from . import marketdb
        return marketdb.read_flows(code)

    def flow_detail(self, code: str) -> pd.DataFrame | None:
        return self.table("flows_detail", code)

    def table(self, dataset: str, code: str) -> pd.DataFrame | None:
        from . import marketdb
        return marketdb.read_dataset(dataset, code)

    def vix(self) -> float | None:
        from . import marketdb
        return marketdb.latest_vix()
