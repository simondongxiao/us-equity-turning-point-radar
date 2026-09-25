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
    source_root = Path(r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener")
    candidates = sorted(p for p in source_root.glob("*/all_metrics.csv") if p.parent.name <= args.as_of)
    all_metrics = candidates[-1] if candidates else None
    mother_count = 0
    source_dates = []
    storage_candidates = []
    if all_metrics and all_metrics.exists():
        with all_metrics.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        rows = [row for row in rows if row.get("base_date") and row["base_date"] <= args.as_of]
        mother_count = len(rows)
        source_dates = sorted({row["base_date"] for row in rows})
        storage_candidates = [row.get("symbol") for row in rows if row.get("symbol") in {"MU", "SNDK", "WDC", "STX", "PSTG", "NTAP"}]
    storage = [r["symbol"] for r in seed if r.get("research_group_id") == "storage-memory"]
    report = {
        "as_of": args.as_of,
        "pool_action": "retain_seed_until_live_popularity_source_is_available",
        "regular_pool_count": len(seed),
        "tech_growth_count": sum(r.get("coverage_bucket") == "科技与成长主题" for r in seed),
        "nontech_count": sum(r.get("coverage_bucket") == "非科技主题" for r in seed),
        "mother_pool_observed_count": mother_count,
        "mother_pool_source": str(all_metrics) if all_metrics else None,
        "mother_pool_data_dates": source_dates,
        "storage_candidates_observed": storage_candidates,
        "weekly_reselection_status": "BLOCKED",
        "weekly_reselection_reason": "Snapshot is available but point-in-time 60-day dollar-volume medians and daily active-rank persistence are not yet validated. Audit only; no automatic selection claim.",
        "mother_pool_qualification": "not_verified_by_radar; source snapshot is read-only",
        "storage_members": storage,
        "storage_candidate_gap": "No independent current popularity history was available in the new radar adapter; no stock was forced into the pool.",
        "historical_pool_preserved": True,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "state" / f"weekly-review-{args.as_of}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
