"""연구용 통계: 블록 부트스트랩 신뢰구간, 제약 있는 순열검정."""
import numpy as np

from .metrics import PPY


def daily_returns(eq: np.ndarray) -> np.ndarray:
    eq = np.asarray(eq, dtype=float)
    return np.diff(eq) / eq[:-1]


def _stat(r: np.ndarray, name: str) -> float:
    if name == "total":
        return float(np.prod(1 + r) - 1)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(PPY)) if sd > 0 else 0.0     # sharpe


def block_bootstrap(a: np.ndarray, b: np.ndarray | None = None, stat: str = "sharpe", block: int = 10, n_boot: int = 5000, seed: int = 0, alpha: float = 0.05) -> dict:
    """원형 블록 부트스트랩. 일별 수익률 a 의 stat 신뢰구간. b 를 주면 같은 날짜 인덱스를 공유해 (a − b) 차이의 신뢰구간을 낸다(짝지은 비교).
    블록 길이는 자기상관(보유 기간 1~5일)을 보존하기 위한 값이다. 표본이 짧고 상승장 한 번이라 구간이 넓게 나오는 것이 정상이다."""
    a = np.asarray(a, dtype=float); n = len(a)
    if b is not None:
        b = np.asarray(b, dtype=float); assert len(b) == n
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    out = np.empty(n_boot)
    for k in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:n]
        out[k] = _stat(a[idx], stat) - (_stat(b[idx], stat) if b is not None else 0.0)
    point = _stat(a, stat) - (_stat(b, stat) if b is not None else 0.0)
    lo, hi = np.quantile(out, [alpha / 2, 1 - alpha / 2])
    return {"point": point, "lo": float(lo), "hi": float(hi), "p_le_0": float((out <= 0).mean())}


def permutation_pvalue(observed: float, null: np.ndarray) -> float:
    """한쪽(관측값 이상) p-value, +1 보정."""
    null = np.asarray(null, dtype=float)
    return float((1 + (null >= observed).sum()) / (1 + len(null)))
