"""Compare current prices with the latest earlier frozen forecast boundaries."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

VERSION = "prior-frozen-tiered-boundary-breach-v1.6.0"
HORIZONS = (5, 10, 21)
HORIZON_LABELS = {5: "5日", 10: "10日", 21: "21日（约20日/1个月）"}
CALIBRATED = {"calibrated", "calibrated_low_confidence"}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _band(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(_finite(v) and float(v) > 0 for v in value):
        return None
    return sorted([float(value[0]), float(value[1])])


def _record_price(record: dict[str, Any]) -> float | None:
    value = record.get("reference_price", record.get("level"))
    return float(value) if _finite(value) and float(value) > 0 else None


def _session_ohlc(record: dict[str, Any]) -> dict[str, float] | None:
    payload = record.get("session_ohlc") or {}
    close = payload.get("close", _record_price(record))
    if not _finite(close) or float(close) <= 0:
        return None
    close = float(close)
    values = {
        "open": float(payload["open"]) if _finite(payload.get("open")) else close,
        "high": float(payload["high"]) if _finite(payload.get("high")) else close,
        "low": float(payload["low"]) if _finite(payload.get("low")) else close,
        "close": close,
    }
    if values["low"] <= 0 or values["high"] <= 0:
        return None
    return values


def _structure_bounds(record: dict[str, Any], horizon: int) -> tuple[float | None, float | None, str]:
    horizon_payload = (((record.get("potential_ranges") or {}).get("horizons") or {}).get(str(horizon)) or {})
    structure = horizon_payload.get("layer1_price_structure") or {}
    core_bottom = structure.get("core_bottom_zone") or {}
    core_top = structure.get("core_top_zone") or {}
    bottom = _band(core_bottom.get("band")) or _band(structure.get("support_band"))
    top = _band(core_top.get("band")) or _band(structure.get("resistance_band"))
    if bottom is None or top is None:
        return None, None, "unavailable"
    return bottom[0], top[1], "prior_frozen_price_structure_outer_edges"


def _terminal_bounds(metric: dict[str, Any]) -> tuple[float | None, float | None, str]:
    terminal = _band(metric.get("terminal_band"))
    if terminal is None and _finite(metric.get("terminal_p10")) and _finite(metric.get("terminal_p90")):
        terminal = _band([metric["terminal_p10"], metric["terminal_p90"]])
    if terminal is None:
        return None, None, "unavailable"
    return terminal[0], terminal[1], "prior_frozen_terminal_p10_p90"


def _pressure_bounds(record: dict[str, Any], metric: dict[str, Any]) -> tuple[float | None, float | None, str]:
    anchor = _record_price(record)
    bottom = _band(metric.get("bottom_zone")) or _band(metric.get("bottom_band")) or _band(metric.get("volatility_bottom_band"))
    top = _band(metric.get("top_zone")) or _band(metric.get("top_band")) or _band(metric.get("volatility_top_band"))
    if anchor is None or bottom is None or top is None:
        return None, None, "unavailable"
    lower_candidates = [bottom[0]]
    upper_candidates = [top[1]]
    for value in (metric.get("terminal_p10"), anchor * (1 + float(metric["expected_return_p05"])) if _finite(metric.get("expected_return_p05")) else None):
        if _finite(value) and float(value) > 0:
            lower_candidates.append(float(value))
    for value in (metric.get("terminal_p90"), anchor * (1 + float(metric["expected_return_p95"])) if _finite(metric.get("expected_return_p95")) else None):
        if _finite(value) and float(value) > 0:
            upper_candidates.append(float(value))
    return min(lower_candidates), max(upper_candidates), "conditional_path_extended_by_terminal_p10_p90_and_net_return_p05_p95"


def _latest_earlier_snapshot(predictions_dir: Path, current_as_of: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    if not predictions_dir.exists():
        return None
    for path in predictions_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if value.get("as_of") and value["as_of"] < current_as_of and value.get("run_id"):
            candidates.append(value)
    if not candidates:
        return None
    return max(candidates, key=lambda value: (value.get("as_of", ""), value.get("generated_at", ""), value.get("run_id", "")))


def _tiered_breaches(
    current: dict[str, Any], frozen: dict[str, Any], metric: dict[str, Any],
    scope: str, horizon: int, layer: str, layer_label: str,
    lower: float | None, upper: float | None, prior: dict[str, Any], current_as_of: str,
) -> list[dict[str, Any]]:
    ohlc = _session_ohlc(current)
    if ohlc is None or lower is None or upper is None:
        return []
    output: list[dict[str, Any]] = []
    checks = (
        ("below_lower", ohlc["low"] < lower, ohlc["close"] < lower, lower, ohlc["low"]),
        ("above_upper", ohlc["high"] > upper, ohlc["close"] > upper, upper, ohlc["high"]),
    )
    for side, intraday, close_confirmed, boundary, extreme in checks:
        if not intraday and not close_confirmed:
            continue
        observed = ohlc["close"] if close_confirmed else extreme
        output.append({
            "symbol": frozen.get("symbol"),
            "name": current.get("name_zh") or current.get("name") or frozen.get("name_zh") or frozen.get("name") or frozen.get("symbol"),
            "scope": scope, "horizon_sessions": horizon, "horizon_label": HORIZON_LABELS[horizon],
            "boundary_layer": layer, "boundary_layer_label": layer_label,
            "side": side, "trigger_state": "close_confirmed" if close_confirmed else "intraday_only",
            "intraday_breached": bool(intraday), "close_confirmed": bool(close_confirmed),
            "session_open": ohlc["open"], "session_high": ohlc["high"],
            "session_low": ohlc["low"], "session_close": ohlc["close"],
            "current_price": ohlc["close"], "observed_price": observed,
            "frozen_lower_bound": lower, "frozen_upper_bound": upper,
            "boundary_value": boundary, "breach_pct": observed / boundary - 1,
            "basis_run_id": prior.get("run_id"), "basis_as_of": prior.get("as_of"),
            "current_as_of": current_as_of,
        })
    return output


def build_boundary_breach_alerts(
    current_records: list[dict[str, Any]],
    current_indices: list[dict[str, Any]],
    current_as_of: str,
    current_run_id: str,
    predictions_dir: Path,
) -> dict[str, Any]:
    prior = _latest_earlier_snapshot(predictions_dir, current_as_of)
    base = {
        "version": VERSION,
        "current_as_of": current_as_of,
        "current_run_id": current_run_id,
        "horizon_labels": {str(h): HORIZON_LABELS[h] for h in HORIZONS},
        "boundary_definition": "分三层比较最近一个更早交易日冻结边界：结构核心带外沿、期限末P10/P90、极端尾部扩展；不是当天重算区间，也不是概率。",
        "comparison_rule": "盘中层使用当日low/high严格跌破/突破；收盘确认使用close严格跌破/突破；等于边界不算越界。",
    }
    if prior is None:
        return {**base, "status": "unavailable", "reason": "no_earlier_frozen_prediction", "basis_run_id": None, "basis_as_of": None, "records": [], "summary": {}, "tiered_records": [], "tiered_summary": {}}

    current_map: dict[tuple[str, str], dict[str, Any]] = {}
    for scope, records in (("stock", current_records), ("index_etf", current_indices)):
        for record in records:
            current_map[(scope, record.get("symbol"))] = record

    prior_rows: list[tuple[str, dict[str, Any]]] = []
    prior_rows.extend(("stock", row) for row in prior.get("records", []))
    prior_rows.extend(("index_etf", row) for row in prior.get("indices", []))
    alerts: list[dict[str, Any]] = []
    tiered_alerts: list[dict[str, Any]] = []
    evaluated = 0
    unavailable = 0
    for scope, frozen in prior_rows:
        symbol = frozen.get("symbol")
        current = current_map.get((scope, symbol))
        current_price = _record_price(current or {})
        if current is None or current_price is None:
            unavailable += len(HORIZONS)
            continue
        for horizon in HORIZONS:
            metric = (frozen.get("metrics") or {}).get(str(horizon), {})
            if metric.get("status") not in CALIBRATED:
                unavailable += 1
                continue
            lower, upper, source = _pressure_bounds(frozen, metric)
            if lower is None or upper is None:
                unavailable += 1
                continue
            evaluated += 1
            structure_lower, structure_upper, _ = _structure_bounds(frozen, horizon)
            terminal_lower, terminal_upper, _ = _terminal_bounds(metric)
            for layer, label, layer_lower, layer_upper in (
                ("structure", "结构核心带", structure_lower, structure_upper),
                ("terminal", "期限末P10/P90", terminal_lower, terminal_upper),
                ("extreme_tail", "极端尾部外沿", lower, upper),
            ):
                tiered_alerts.extend(_tiered_breaches(
                    current, frozen, metric, scope, horizon, layer, label,
                    layer_lower, layer_upper, prior, current_as_of,
                ))
            side = None
            boundary = None
            breach_pct = None
            if current_price < lower:
                side, boundary, breach_pct = "below_lower", lower, current_price / lower - 1
            elif current_price > upper:
                side, boundary, breach_pct = "above_upper", upper, current_price / upper - 1
            if side is None:
                continue
            alerts.append({
                "symbol": symbol,
                "name": current.get("name_zh") or current.get("name") or frozen.get("name_zh") or frozen.get("name") or symbol,
                "scope": scope,
                "horizon_sessions": horizon,
                "horizon_label": HORIZON_LABELS[horizon],
                "side": side,
                "current_price": current_price,
                "frozen_lower_bound": lower,
                "frozen_upper_bound": upper,
                "boundary_value": boundary,
                "breach_pct": breach_pct,
                "boundary_source": source,
                "basis_run_id": prior.get("run_id"),
                "basis_as_of": prior.get("as_of"),
                "current_as_of": current_as_of,
            })
    alerts.sort(key=lambda item: (item["horizon_sessions"], item["side"], -abs(item["breach_pct"]), item["symbol"]))
    layer_order = {"structure": 0, "terminal": 1, "extreme_tail": 2}
    tiered_alerts.sort(key=lambda item: (item["horizon_sessions"], layer_order[item["boundary_layer"]], item["side"], 0 if item["trigger_state"] == "close_confirmed" else 1, -abs(item["breach_pct"]), item["symbol"]))
    summary = {
        str(h): {
            "below_lower": sum(item["horizon_sessions"] == h and item["side"] == "below_lower" for item in alerts),
            "above_upper": sum(item["horizon_sessions"] == h and item["side"] == "above_upper" for item in alerts),
        }
        for h in HORIZONS
    }
    tiered_summary = {
        str(h): {
            layer: {
                side: {
                    trigger: sum(
                        item["horizon_sessions"] == h and item["boundary_layer"] == layer
                        and item["side"] == side and item["trigger_state"] == trigger
                        for item in tiered_alerts
                    )
                    for trigger in ("intraday_only", "close_confirmed")
                }
                for side in ("below_lower", "above_upper")
            }
            for layer in ("structure", "terminal", "extreme_tail")
        }
        for h in HORIZONS
    }
    return {
        **base,
        "status": "observed",
        "basis_run_id": prior.get("run_id"),
        "basis_as_of": prior.get("as_of"),
        "evaluated_symbol_horizons": evaluated,
        "unavailable_symbol_horizons": unavailable,
        "records": alerts,
        "summary": summary,
        "tiered_records": tiered_alerts,
        "tiered_summary": tiered_summary,
        "note": "这里只报告已越界；未出现的标的不代表方向安全。盘中越界与收盘确认分开，结构带、期限末带和极端尾部不可混为同一强度，也都不等于立即反转或交易信号。",
    }

