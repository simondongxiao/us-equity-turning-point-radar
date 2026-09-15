"""Weekly pool and storage coverage audit without mutating the 100-name pool."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = parser.parse_args()
    seed = list(csv.DictReader((ROOT / "assets" / "universe_seed.csv").open(encoding="utf-8-sig", newline="")))
    all_metrics = Path(r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener\2026-09-13\all_metrics.csv")
    mother_count = 0
    if all_metrics.exists():
        with all_metrics.open(encoding="utf-8-sig", newline="") as fh:
            mother_count = sum(1 for _ in csv.DictReader(fh))
    storage = [r["symbol"] for r in seed if r.get("research_group_id") == "storage-memory"]
    report = {
        "as_of": args.as_of,
        "pool_action": "retain_seed_until_live_popularity_source_is_available",
        "regular_pool_count": len(seed),
        "tech_growth_count": sum(r.get("coverage_bucket") == "科技与成长主题" for r in seed),
        "nontech_count": sum(r.get("coverage_bucket") == "非科技主题" for r in seed),
        "mother_pool_observed_count": mother_count,
        "mother_pool_qualification": "not_verified_by_radar; source snapshot is read-only",
        "storage_members": storage,
        "storage_candidate_gap": "No independent current popularity history was available in the new radar adapter; no stock was forced into the pool.",
        "historical_pool_preserved": True,
    }
    out = ROOT / "state" / f"weekly-review-{args.as_of}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
