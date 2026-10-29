"""CSV(data/) + SQLite(data/trading.db) → PostgreSQL 1회성 이전. 여러 번 실행해도 안전(upsert/ON CONFLICT). 원본 파일은 삭제하지 않는다.
사용: python scripts/migrate_to_postgres.py"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockwatch import config, db, marketdb  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
DATASETS = ["flows_detail", "foreign_own", "market_cap", "short_trading", "short_balance", "fundamental", "index", "market_flow"]


def csvs(folder: str):
    return sorted((DATA / folder).glob("*.csv")) if (DATA / folder).is_dir() else []


def main():
    d = db.get()
    print(f"대상: schema={d.schema}")
    config.load()  # 관심 종목 시드(config.yaml → watchlist, 최초 1회)
    n = lambda t: d.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]

    for f in csvs("prices"):
        marketdb.upsert_prices(f.stem, pd.read_csv(f, parse_dates=["date"])); print("  prices", f.stem)
    for f in csvs("flows"):
        marketdb.upsert_flows(f.stem, pd.read_csv(f, parse_dates=["date"])); print("  flows", f.stem)
    if (DATA / "vix.csv").exists():
        marketdb.upsert_vix(pd.read_csv(DATA / "vix.csv", parse_dates=["date"])); print("  vix")
    for ds in DATASETS:
        for f in csvs(ds):
            marketdb.save_dataset(ds, f.stem, pd.read_csv(f, parse_dates=["date"])); print(f"  {ds}", f.stem)

    sq = DATA / "trading.db"
    if sq.exists():
        s = sqlite3.connect(sq); s.row_factory = sqlite3.Row
        with d.tx() as c:
            for t, cols, conflict in (
                ("settings", ["key", "value"], "(key)"), ("rules", ["code", "budget_krw", "live", "protect", "stop_atr", "target_atr"], "(code)"),
                ("push_tokens", ["token", "created_at"], "(token)"),
                ("proposals", ["id", "created_at", "decided_at", "code", "name", "side", "status", "qty", "price", "stop", "target", "evidence",
                               "sizing", "dry_run", "order_id", "protect_order_id", "result"], "(id)"),
                ("ledger", ["id", "ts", "code", "side", "qty", "price", "fee", "proposal_id"], "(id)"),
                ("audit", ["id", "ts", "event", "detail"], "(id)")):
                rows = [tuple(r[k] for k in cols) for r in s.execute(f"SELECT {','.join(cols)} FROM {t}")]
                for row in rows:
                    c.execute(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) ON CONFLICT {conflict} DO NOTHING", row)
                print(f"  sqlite {t}: {len(rows)}행")
            for t in ("proposals", "ledger", "audit"):  # 이전한 id 보다 큰 값부터 발급되도록 시퀀스 보정
                c.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), GREATEST(COALESCE((SELECT MAX(id) FROM {t}),0),1))")

    print("\n== PostgreSQL 행 수 ==")
    for t in ("watchlist", "prices", "flows", "vix", "datasets", "rules", "proposals", "ledger", "audit", "settings", "push_tokens"):
        print(f"  {t:12} {n(t):>7}")


if __name__ == "__main__":
    main()
