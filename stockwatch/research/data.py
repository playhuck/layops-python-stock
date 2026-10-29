"""연구용 가격 데이터 정리."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")
FINAL_AFTER = time(15, 40)    # 정규장 마감 15:30 + 여유. 이 시각 전에 받은 오늘 봉은 장중 값일 수 있다


def complete_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """장 마감 전에는 오늘 봉(미완성)을 제외한다. 미청산 포지션 평가나 마지막 신호가 장중 값에 흔들리는 것을 막는다."""
    now = now.astimezone(KST) if now else datetime.now(KST)
    if len(df) and df.index[-1].date() == now.date() and now.time() < FINAL_AFTER:
        return df.iloc[:-1]
    return df
