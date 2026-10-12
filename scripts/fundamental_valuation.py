"""Point-in-time consensus EPS and peer-valuation theoretical bounds.

This layer is deliberately separate from the short-horizon path model.  It
may explain whether price is above or below a fundamental valuation range,
but it must not alter calibrated turning probabilities until archived
consensus histories support an out-of-sample test.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import requests


NASDAQ_EPS_URL = "https://api.nasdaq.com/api/analyst/{symbol}/earnings-forecast"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def _number(value: Any) -> float | None:
    try:
        result = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_yearly_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (((payload.get("data") or {}).get("yearlyForecast") or {}).get("rows") or [])
    output: list[dict[str, Any]] = []
    for row in rows:
        consensus = _number(row.get("consensusEPSForecast"))
        low = _number(row.get("lowEPSForecast"))
        high = _number(row.get("highEPSForecast"))
        estimates = _number(row.get("noOfEstimates"))
        if not row.get("fiscalEnd") or consensus is None:
            continue
        output.append({
            "fiscal_end": str(row["fiscalEnd"]),
            "eps_consensus": consensus,
            "eps_low": low,
            "eps_high": high,
            "estimate_count": int(estimates) if estimates is not None else None,
            "revisions_up_4w": int(_number(row.get("up")) or 0),
            "revisions_down_4w": int(_number(row.get("down")) or 0),
        })
    return output


def fetch_symbol_consensus(symbol: str, session: requests.Session | None = None,
                           attempts: int = 3) -> dict[str, Any]:
    session = session or requests.Session()
    url = NASDAQ_EPS_URL.format(symbol=symbol.upper())
    last_error = "unknown_error"
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            rows = _parse_yearly_rows(response.json())
            if not rows:
                return {"status": "unavailable", "reason": "no_yearly_consensus_rows", "source_url": url}
            return {"status": "observed", "yearly_forecasts": rows, "source_url": url}
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    return {"status": "unavailable", "reason": last_error, "source_url": url}


def fetch_consensus_snapshot(symbols: list[str], as_of: str, state_dir: Path,
                             refresh: bool = True, max_workers: int = 4) -> dict[str, Any]:
    """Fetch once per completed market date and retain an immutable raw snapshot."""
    target = state_dir / "fundamentals" / f"consensus-{as_of}.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    ny_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    if not refresh:
        return {"status": "unavailable", "reason": "refresh_disabled_and_no_frozen_snapshot", "as_of": as_of, "records": {}}
    if ny_date != as_of:
        return {"status": "unavailable", "reason": "historical_consensus_backfill_forbidden", "as_of": as_of,
                "retrieval_market_date": ny_date, "records": {}}
    records: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 6))) as pool:
        jobs = {pool.submit(fetch_symbol_consensus, symbol): symbol for symbol in symbols}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                records[symbol] = job.result()
            except Exception as exc:  # isolate one provider failure from the 100-stock run
                records[symbol] = {"status": "unavailable", "reason": type(exc).__name__,
                                   "source_url": NASDAQ_EPS_URL.format(symbol=symbol)}
    observed = sum(row.get("status") == "observed" for row in records.values())
    snapshot = {
        "status": "observed" if observed else "unavailable",
        "as_of": as_of,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "retrieval_market_date": ny_date,
        "provider": "Nasdaq analyst earnings forecast API",
        "requested_symbols": len(symbols),
        "observed_symbols": observed,
        "records": records,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return snapshot


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    array = np.asarray(values, dtype=float)
    return tuple(float(v) for v in np.quantile(array, [0.25, 0.50, 0.75]))


def build_valuation_bounds(records: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach next-fiscal-year EPS × peer forward-P/E theoretical bounds."""
    source_records = snapshot.get("records") or {}
    prepared: dict[str, dict[str, Any]] = {}
    for record in records:
        symbol = record["symbol"]
        raw = source_records.get(symbol) or {}
        rows = raw.get("yearly_forecasts") or []
        forecast = rows[0] if rows else None
        price = _number(record.get("reference_price"))
        consensus = _number((forecast or {}).get("eps_consensus"))
        if raw.get("status") != "observed" or not forecast:
            prepared[symbol] = {"status": "unavailable", "reason": raw.get("reason", "consensus_unavailable"),
                                "source_url": raw.get("source_url")}
            continue
        if price is None or price <= 0:
            prepared[symbol] = {"status": "unavailable", "reason": "reference_price_unavailable",
                                "source_url": raw.get("source_url")}
            continue
        if consensus is None or consensus <= 0:
            prepared[symbol] = {"status": "unavailable", "reason": "nonpositive_eps_consensus_requires_non_pe_method",
                                "forecast": forecast, "source_url": raw.get("source_url")}
            continue
        prepared[symbol] = {
            "status": "prepared", "forecast": forecast, "implied_forward_pe": price / consensus,
            "source_url": raw.get("source_url"), "reference_price": price,
            "research_group": record.get("research_group"), "coverage_bucket": record.get("coverage_bucket"),
        }

    results: dict[str, dict[str, Any]] = {}
    for record in records:
        symbol = record["symbol"]
        own = prepared.get(symbol) or {"status": "unavailable", "reason": "consensus_unavailable"}
        if own.get("status") != "prepared":
            results[symbol] = own
            continue
        peers = [x["implied_forward_pe"] for peer_symbol, x in prepared.items()
                 if peer_symbol != symbol and x.get("status") == "prepared"
                 and x.get("research_group") == own.get("research_group")
                 and 2.0 <= x["implied_forward_pe"] <= 150.0]
        basis = "same_research_group_ex_self"
        if len(peers) < 3:
            peers = [x["implied_forward_pe"] for peer_symbol, x in prepared.items()
                     if peer_symbol != symbol and x.get("status") == "prepared"
                     and x.get("coverage_bucket") == own.get("coverage_bucket")
                     and 2.0 <= x["implied_forward_pe"] <= 150.0]
            basis = "coverage_bucket_fallback_ex_self"
        if len(peers) < 3:
            peers = [x["implied_forward_pe"] for peer_symbol, x in prepared.items()
                     if peer_symbol != symbol and x.get("status") == "prepared"
                     and 2.0 <= x["implied_forward_pe"] <= 150.0]
            basis = "full_universe_fallback_ex_self"
        if len(peers) < 3:
            results[symbol] = {"status": "unavailable", "reason": "insufficient_positive_eps_peer_multiples",
                               "forecast": own["forecast"], "source_url": own.get("source_url")}
            continue
        pe25, pe50, pe75 = _percentiles(peers)
        forecast = own["forecast"]
        eps_low = _number(forecast.get("eps_low"))
        eps_high = _number(forecast.get("eps_high"))
        eps_consensus = _number(forecast.get("eps_consensus"))
        if eps_low is None or eps_high is None or eps_low <= 0 or eps_high <= 0:
            results[symbol] = {"status": "unavailable", "reason": "positive_eps_low_high_required",
                               "forecast": forecast, "source_url": own.get("source_url")}
            continue
        # A cross-company P/E can reflect durable business-mix differences.
        # Bound one-year multiple re-rating to +/-25% around the stock's own
        # current implied forward P/E, while retaining raw peer percentiles for
        # audit. This keeps peer valuation informative without allowing an HDD,
        # bank or early-profitability peer to dominate another business model.
        own_pe = own["implied_forward_pe"]
        pe_guard_low, pe_guard_high = own_pe * 0.75, own_pe * 1.25
        applied_pe25 = float(np.clip(pe25, pe_guard_low, pe_guard_high))
        applied_pe50 = float(np.clip(pe50, pe_guard_low, pe_guard_high))
        applied_pe75 = float(np.clip(pe75, pe_guard_low, pe_guard_high))
        applied_pe25, applied_pe50, applied_pe75 = sorted((applied_pe25, applied_pe50, applied_pe75))
        lower = eps_low * applied_pe25
        central = eps_consensus * applied_pe50
        upper = eps_high * applied_pe75
        estimate_count = int(forecast.get("estimate_count") or 0)
        confidence = "high" if estimate_count >= 5 and len(peers) >= 5 else "medium" if estimate_count >= 3 else "low"
        price = own["reference_price"]
        position = "below_theoretical_lower" if price < lower else "above_theoretical_upper" if price > upper else "inside_theoretical_range"
        results[symbol] = {
            "status": "observed",
            "method": "next_fiscal_year_consensus_EPS_low/mean/high x guarded_ex-self_peer_forward_PE_P25/P50/P75",
            "theoretical_lower": lower,
            "theoretical_central": central,
            "theoretical_upper": upper,
            "forecast_fiscal_end": forecast.get("fiscal_end"),
            "eps_low": eps_low,
            "eps_consensus": eps_consensus,
            "eps_high": eps_high,
            "estimate_count": estimate_count,
            "revisions_up_4w": forecast.get("revisions_up_4w"),
            "revisions_down_4w": forecast.get("revisions_down_4w"),
            "implied_forward_pe": own["implied_forward_pe"],
            "peer_forward_pe_p25": pe25,
            "peer_forward_pe_p50": pe50,
            "peer_forward_pe_p75": pe75,
            "applied_forward_pe_lower": applied_pe25,
            "applied_forward_pe_central": applied_pe50,
            "applied_forward_pe_upper": applied_pe75,
            "own_forward_pe_guard_low": pe_guard_low,
            "own_forward_pe_guard_high": pe_guard_high,
            "multiple_guard_method": "raw peer percentile clipped to 0.75x-1.25x own implied forward PE",
            "peer_sample_count": len(peers),
            "peer_basis": basis,
            "reference_price": price,
            "price_position": position,
            "distance_to_lower_pct": price / lower - 1.0,
            "distance_to_upper_pct": price / upper - 1.0,
            "confidence": confidence,
            "source_url": own.get("source_url"),
            "source_provider": snapshot.get("provider"),
            "source_retrieved_at": snapshot.get("retrieved_at"),
            "point_in_time_as_of": snapshot.get("as_of"),
            "probability_integration_status": "DISPLAY_ONLY_PENDING_ARCHIVED_CONSENSUS_BACKTEST",
            "note": "理论估值上下界是中期基本面锚，不代表5/10/21日内必然触达或反转。",
        }
    observed = sum(row.get("status") == "observed" for row in results.values())
    return {
        "status": "observed" if observed else "unavailable",
        "as_of": snapshot.get("as_of"),
        "retrieved_at": snapshot.get("retrieved_at"),
        "provider": snapshot.get("provider"),
        "requested_symbols": len(records),
        "observed_symbols": observed,
        "method": "next-fiscal-year consensus EPS range multiplied by ex-self peer forward-P/E quartiles, guarded to 0.75x-1.25x own implied forward P/E",
        "probability_integration_status": "DISPLAY_ONLY_PENDING_ARCHIVED_CONSENSUS_BACKTEST",
        "records": results,
    }
