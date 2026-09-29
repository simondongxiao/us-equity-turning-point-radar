"""Point-in-time weekly pool and storage coverage audit.

The legacy US screener is read-only. This adapter computes the documented
price/volume popularity inputs from the historical bars stored beside a
dated screener snapshot, but it never mutates the production 100-name pool.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(
    r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener"
)
STORAGE_CANDIDATES = {"MU", "SNDK", "WDC", "STX", "PSTG", "NTAP"}
KNOWN_SAME_ISSUER_CLASSES = {"GOOGL": "GOOG", "BRK-B": "BRK.A"}
POPULARITY_VERSION = "price-volume-pit-v1"


def latest_eligible_snapshot(source_root: Path, as_of: str) -> tuple[Path | None, list[dict[str, str]]]:
    """Choose by the data's base_date, not by the directory/build date."""
    candidates = sorted(source_root.glob("*/all_metrics.csv"), reverse=True)
    for path in candidates:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = [
                row
                for row in csv.DictReader(fh)
                if row.get("base_date") and row["base_date"] <= as_of
            ]
        if rows:
            return path, rows
    return None, []


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed if np.isfinite(parsed) else float("nan")


def _load_universe_metadata(snapshot_dir: Path, metric_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    metric_by_symbol = {row.get("symbol", "").upper(): row for row in metric_rows if row.get("symbol")}
    filtered_path = snapshot_dir / "universe_filtered.csv"
    if not filtered_path.exists():
        return metric_by_symbol
    with filtered_path.open(encoding="utf-8-sig", newline="") as fh:
        filtered = {row.get("symbol", "").upper(): row for row in csv.DictReader(fh) if row.get("symbol")}
    for symbol, row in filtered.items():
        source = metric_by_symbol.get(symbol, {})
        for key in ("sector", "marketCap", "marketCapSource", "data_coverage_score"):
            if source.get(key) not in (None, ""):
                row[key] = source[key]
    return filtered


def compute_point_in_time_popularity(
    charts: dict[str, pd.DataFrame],
    metadata: dict[str, dict[str, str]],
    as_of: str,
    *,
    active_top_n: int = 300,
) -> pd.DataFrame:
    """Compute the documented four-component popularity score.

    Dollar turnover uses close*volume because the read-only source does not
    expose consolidated trade value. A symbol needs 60 valid sessions for a
    complete score and must pass the current price/20-day median-dollar gate.
    Daily active persistence is evaluated only against securities eligible on
    that historical date, preventing use of a future mother pool.
    """
    cutoff = pd.Timestamp(as_of)
    current_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    allowed = set(metadata)

    for raw_symbol, frame in charts.items():
        symbol = str(raw_symbol).upper()
        if symbol not in allowed or not isinstance(frame, pd.DataFrame):
            continue
        required = {"date", "close", "volume"}
        if not required.issubset(frame.columns):
            continue
        work = frame.loc[:, ["date", "close", "volume"]].copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work["close"] = pd.to_numeric(work["close"], errors="coerce")
        work["volume"] = pd.to_numeric(work["volume"], errors="coerce")
        work = work.dropna().sort_values("date")
        work = work[(work["date"] <= cutoff) & (work["close"] > 0) & (work["volume"] >= 0)]
        work = work.drop_duplicates("date", keep="last").tail(119)
        if work.empty:
            continue
        work["dollar_volume"] = work["close"] * work["volume"]
        work["median_dollar_5d"] = work["dollar_volume"].rolling(5, min_periods=5).median()
        work["median_dollar_20d"] = work["dollar_volume"].rolling(20, min_periods=20).median()
        work["median_dollar_60d"] = work["dollar_volume"].rolling(60, min_periods=60).median()
        work["eligible_on_date"] = (
            (work["close"] >= 3.0)
            & (work["median_dollar_20d"] >= 50_000_000.0)
        )
        last = work.iloc[-1]
        data_date = last["date"].strftime("%Y-%m-%d")
        history_sessions = int(len(work))
        complete_60d = bool(pd.notna(last["median_dollar_60d"]))
        complete_persistence = history_sessions >= 79
        current_eligible = bool(last["eligible_on_date"] and data_date == as_of)
        reasons: list[str] = []
        if data_date != as_of:
            reasons.append("stale_price_date")
        if last["close"] < 3.0:
            reasons.append("price_below_3")
        if history_sessions < 20:
            reasons.append("fewer_than_20_sessions")
        if pd.isna(last["median_dollar_20d"]) or last["median_dollar_20d"] < 50_000_000.0:
            reasons.append("median_dollar_20d_below_50m")
        popularity_reasons: list[str] = []
        if not complete_60d:
            popularity_reasons.append("incomplete_60_session_dollar_volume_history")
        if not complete_persistence:
            popularity_reasons.append("incomplete_20_session_pre_window_for_60d_persistence")
        meta = metadata[symbol]
        current_rows.append(
            {
                "symbol": symbol,
                "name": meta.get("name"),
                "exchange": meta.get("exchange"),
                "cik": meta.get("cik"),
                "source_sector": meta.get("sector"),
                "data_date": data_date,
                "price": float(last["close"]),
                "history_sessions": history_sessions,
                "median_dollar_5d": _number(last["median_dollar_5d"]),
                "median_dollar_20d": _number(last["median_dollar_20d"]),
                "median_dollar_60d": _number(last["median_dollar_60d"]),
                "qualified": current_eligible,
                "qualification_reasons": reasons,
                "popularity_complete": bool(current_eligible and complete_60d and complete_persistence),
                "popularity_incomplete_reasons": popularity_reasons,
            }
        )
        daily = work.tail(60).loc[:, ["date", "dollar_volume", "eligible_on_date"]].copy()
        daily["symbol"] = symbol
        daily_rows.append(daily)

    current = pd.DataFrame(current_rows)
    if current.empty:
        return current
    daily = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    if not daily.empty:
        dates = sorted(daily["date"].dropna().unique())[-60:]
        daily = daily[daily["date"].isin(dates)]
        daily["active_rank"] = np.nan
        eligible_mask = daily["eligible_on_date"]
        daily.loc[eligible_mask, "active_rank"] = (
            daily.loc[eligible_mask]
            .groupby("date")["dollar_volume"]
            .rank(method="first", ascending=False)
        )
        daily["active_top"] = daily["active_rank"].le(active_top_n)
        activity = daily.groupby("symbol").agg(
            active_top_ratio=("active_top", "mean"),
            active_observed_days=("date", "nunique"),
            active_eligible_days=("eligible_on_date", "sum"),
        )
        current = current.merge(activity, how="left", left_on="symbol", right_index=True)
    else:
        current["active_top_ratio"] = np.nan
        current["active_observed_days"] = 0
        current["active_eligible_days"] = 0

    current["activity_change_raw"] = current["median_dollar_5d"] / current["median_dollar_60d"]
    score_set = current.loc[current["qualified"] & current["popularity_complete"]].copy()
    if not score_set.empty:
        score_set["pctl_median_dollar_60d"] = score_set["median_dollar_60d"].rank(pct=True)
        score_set["pctl_median_dollar_20d"] = score_set["median_dollar_20d"].rank(pct=True)
        score_set["pctl_active_persistence"] = score_set["active_top_ratio"].rank(pct=True)
        lower = score_set["activity_change_raw"].quantile(0.01)
        upper = score_set["activity_change_raw"].quantile(0.99)
        score_set["activity_change_winsorized"] = score_set["activity_change_raw"].clip(lower, upper)
        score_set["pctl_activity_change"] = score_set["activity_change_winsorized"].rank(pct=True)
        score_set["popularity_score"] = 100.0 * (
            0.45 * score_set["pctl_median_dollar_60d"]
            + 0.25 * score_set["pctl_median_dollar_20d"]
            + 0.20 * score_set["pctl_active_persistence"]
            + 0.10 * score_set["pctl_activity_change"]
        )
        score_set = score_set.sort_values(["popularity_score", "symbol"], ascending=[False, True])
        score_set["raw_popularity_rank"] = np.arange(1, len(score_set) + 1)
        score_set["issuer_key"] = score_set.apply(
            lambda row: f"cik:{row['cik']}" if str(row.get("cik") or "").strip() else f"unknown:{row['symbol']}",
            axis=1,
        )
        score_set["issuer_selected"] = ~score_set.duplicated("issuer_key", keep="first")
        selected = score_set[score_set["issuer_selected"]].copy()
        selected["issuer_popularity_rank"] = np.arange(1, len(selected) + 1)
        score_set = score_set.merge(
            selected.loc[:, ["symbol", "issuer_popularity_rank"]], how="left", on="symbol"
        )
        add_columns = [
            "symbol",
            "pctl_median_dollar_60d",
            "pctl_median_dollar_20d",
            "pctl_active_persistence",
            "activity_change_winsorized",
            "pctl_activity_change",
            "popularity_score",
            "raw_popularity_rank",
            "issuer_key",
            "issuer_selected",
            "issuer_popularity_rank",
        ]
        current = current.merge(score_set.loc[:, add_columns], how="left", on="symbol")
    else:
        current["popularity_score"] = np.nan
        current["raw_popularity_rank"] = np.nan
        current["issuer_selected"] = False
        current["issuer_popularity_rank"] = np.nan
    return current.sort_values(["qualified", "popularity_score", "symbol"], ascending=[False, False, True])


def _json_records(frame: pd.DataFrame, columns: list[str], limit: int | None = None) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    available = [column for column in columns if column in frame.columns]
    selected = frame.loc[:, available]
    if limit is not None:
        selected = selected.head(limit)
    selected = selected.replace({np.nan: None})
    return selected.to_dict(orient="records")


def build_report(
    *,
    as_of: str,
    seed: list[dict[str, str]],
    all_metrics: Path | None,
    rows: list[dict[str, str]],
    active_top_n: int,
) -> dict[str, Any]:
    storage = [row["symbol"] for row in seed if row.get("research_group_id") == "storage-memory"]
    source_dates = sorted({row["base_date"] for row in rows if row.get("base_date")})
    base: dict[str, Any] = {
        "as_of": as_of,
        "pool_action": "retain_seed_pending_complete_weekly_promotion_gate",
        "regular_pool_count": len(seed),
        "tech_growth_count": sum(row.get("coverage_bucket") == "科技与成长主题" for row in seed),
        "nontech_count": sum(row.get("coverage_bucket") == "非科技主题" for row in seed),
        "mother_pool_observed_count": len(rows),
        "mother_pool_source": str(all_metrics) if all_metrics else None,
        "mother_pool_data_dates": source_dates,
        "storage_members": storage,
        "historical_pool_preserved": True,
    }
    if not all_metrics:
        base.update(
            {
                "weekly_reselection_status": "BLOCKED",
                "weekly_reselection_reason": "No eligible read-only mother-pool snapshot was found.",
                "mother_pool_qualification": "not_available",
                "storage_candidates_observed": [],
            }
        )
        return base

    snapshot_dir = all_metrics.parent
    chart_path = snapshot_dir / "full_charts.pkl"
    if not chart_path.exists():
        base.update(
            {
                "weekly_reselection_status": "BLOCKED",
                "weekly_reselection_reason": "Mother-pool summary exists but point-in-time chart archive is missing.",
                "mother_pool_qualification": "summary_only",
                "storage_candidates_observed": [],
            }
        )
        return base

    metadata = _load_universe_metadata(snapshot_dir, rows)
    charts = pd.read_pickle(chart_path)
    if not isinstance(charts, dict):
        raise TypeError(f"Expected dict in {chart_path}, got {type(charts).__name__}")
    scored = compute_point_in_time_popularity(
        charts, metadata, as_of, active_top_n=active_top_n
    )
    if "issuer_selected" in scored.columns:
        qualified = scored[(scored["qualified"]) & (scored["issuer_selected"] == True)].copy()  # noqa: E712
    else:
        qualified = scored.iloc[0:0].copy()
    seed_symbols = {row["symbol"] for row in seed}
    member_audit = scored[scored["symbol"].isin(seed_symbols)].copy()
    missing_members = sorted(seed_symbols - set(member_audit["symbol"]))
    current_member_source_gaps = [
        {
            "symbol": symbol,
            "same_issuer_other_class": KNOWN_SAME_ISSUER_CLASSES.get(symbol),
            "status": "other_share_class_visible_not_used_as_liquidity_substitute"
            if KNOWN_SAME_ISSUER_CLASSES.get(symbol) in charts
            else "exact_security_missing",
        }
        for symbol in missing_members
    ]
    storage_audit = scored[scored["symbol"].isin(STORAGE_CANDIDATES)].copy()
    ranking_columns = [
        "symbol",
        "name",
        "exchange",
        "cik",
        "source_sector",
        "data_date",
        "price",
        "median_dollar_20d",
        "median_dollar_60d",
        "active_top_ratio",
        "active_observed_days",
        "activity_change_winsorized",
        "popularity_score",
        "issuer_popularity_rank",
        "qualified",
        "qualification_reasons",
        "popularity_complete",
        "popularity_incomplete_reasons",
    ]
    base.update(
        {
            "popularity_model": {
                "version": POPULARITY_VERSION,
                "weights": {
                    "median_dollar_60d_percentile": 0.45,
                    "median_dollar_20d_percentile": 0.25,
                    "daily_active_top_persistence": 0.20,
                    "winsorized_5d_vs_60d_activity_change": 0.10,
                },
                "qualification": {
                    "minimum_price": 3.0,
                    "minimum_median_dollar_20d": 50_000_000.0,
                    "minimum_valid_sessions": 20,
                    "complete_popularity_sessions": 60,
                    "pre_window_sessions_for_daily_eligibility": 19,
                },
                "active_top_n": active_top_n,
                "dollar_volume_basis": "close_x_volume_proxy",
                "point_in_time_policy": "each daily rank uses only securities eligible on that date",
                "identity_policy": "universe_filtered common-security gate; one security per known SEC CIK; unknown CIK not claimed deduplicated",
            },
            "mother_pool_qualification": "observed_price_volume_proxy_not_promoted",
            "chart_archive_source": str(chart_path),
            "chart_symbols_observed": len(charts),
            "scored_symbols": int(len(scored)),
            "qualified_security_count": int(scored["qualified"].sum()) if not scored.empty else 0,
            "complete_popularity_security_count": int(scored["popularity_complete"].sum()) if not scored.empty else 0,
            "qualified_issuer_count": int(len(qualified)),
            "current_member_audit_count": int(len(member_audit)),
            "current_member_missing": missing_members,
            "current_member_source_gaps": current_member_source_gaps,
            "current_member_qualified_count": int(member_audit["qualified"].sum()) if not member_audit.empty else 0,
            "current_member_complete_popularity_count": int(member_audit["popularity_complete"].sum()) if not member_audit.empty else 0,
            "candidate_preview": _json_records(qualified, ranking_columns, limit=150),
            "current_member_audit": _json_records(member_audit, ranking_columns),
            "storage_candidates_observed": sorted(storage_audit["symbol"].tolist()),
            "storage_candidate_audit": _json_records(storage_audit, ranking_columns),
            "storage_candidate_gap": "PSTG/NTAP are broader research candidates only; they are not forced into the regular pool or storage main group.",
            "weekly_reselection_status": "BLOCKED",
            "weekly_reselection_reason": (
                "Point-in-time price/volume popularity is now computed, but automatic replacement remains gated: "
                "the source's split/volume alignment is inherited rather than independently certified, new-candidate "
                "research coverage buckets are not formally mapped to the radar taxonomy, and this local read-only "
                "mother-pool archive is not available to the GitHub runner. No production member was changed."
            ),
        }
    )
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--active-top-n", type=int, default=300)
    args = parser.parse_args()
    seed = list(csv.DictReader((ROOT / "assets" / "universe_seed.csv").open(encoding="utf-8-sig", newline="")))
    all_metrics, rows = latest_eligible_snapshot(args.source_root, args.as_of)
    report = build_report(
        as_of=args.as_of,
        seed=seed,
        all_metrics=all_metrics,
        rows=rows,
        active_top_n=args.active_top_n,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "state" / f"weekly-review-{args.as_of}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
