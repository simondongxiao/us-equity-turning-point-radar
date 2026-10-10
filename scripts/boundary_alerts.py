"""Compare current prices with the latest earlier frozen forecast boundaries."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

VERSION = "prior-frozen-extreme-atr-breach-v1.7.0"
HORIZONS = (5, 10, 21)
HORIZON_LABELS = {5: "5日", 10: "10日", 21: "21日（约20日/1个月）"}
CALIBRATED = {"calibrated", "calibrated_low_confidence"}
EXTREME_ATR_MULTIPLIER = 2.0
VOLATILITY_BAND_OUTER_ATR_MULTIPLIER = 1.25


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


def _extreme_atr_bounds(
    record: dict[str, Any], metric: dict[str, Any], horizon: int,
) -> tuple[float | None, float | None, float | None, str]:
    anchor = _record_price(record)
    if anchor is None:
        return None, None, None, "unavailable"
    horizon_payload = (((record.get("potential_ranges") or {}).get("horizons") or {}).get(str(horizon)) or {})
    structure = horizon_payload.get("layer1_price_structure") or {}
    atr = structure.get("atr14")
    source = "prior_frozen_structure_atr14"
    scale = math.sqrt(horizon / 5.0)
    if not _finite(atr) or float(atr) <= 0:
        inferred: list[float] = []
        bottom = _band(metric.get("volatility_bottom_band"))
        top = _band(metric.get("volatility_top_band"))
        if bottom is not None and bottom[0] < anchor:
            inferred.append((anchor - bottom[0]) / (VOLATILITY_BAND_OUTER_ATR_MULTIPLIER * scale))
        if top is not None and top[1] > anchor:
            inferred.append((top[1] - anchor) / (VOLATILITY_BAND_OUTER_ATR_MULTIPLIER * scale))
        inferred = [value for value in inferred if _finite(value) and value > 0]
        if not inferred:
            return None, None, None, "unavailable"
        atr = sum(inferred) / len(inferred)
        source = "prior_frozen_atr14_inferred_from_volatility_outer_band"
    atr = float(atr)
    distance = EXTREME_ATR_MULTIPLIER * atr * scale
    return max(0.01, anchor - distance), anchor + distance, atr, source


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
    lower: float | None, upper: float | None, atr14: float | None,
    prior: dict[str, Any], current_as_of: str, boundary_source: str,
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
            "boundary_source": boundary_source,
            "frozen_anchor_price": _record_price(frozen),
            "frozen_atr14": atr14,
            "extreme_atr_multiplier": EXTREME_ATR_MULTIPLIER,
            "horizon_atr_scale": math.sqrt(horizon / 5.0),
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
        "boundary_definition": "只比较最近一个更早交易日冻结的极端ATR边界：冻结参考价 ± 2.0 × ATR14 × sqrt(H/5)；不是结构支撑阻力、期限末分位或概率。",
        "comparison_rule": "当日low/high严格跌破/突破极端ATR边界才显示；close仍在边界外为收盘确认，否则为仅盘中；等于边界不算越界。",
        "extreme_atr_multiplier": EXTREME_ATR_MULTIPLIER,
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
            lower, upper, atr14, source = _extreme_atr_bounds(frozen, metric, horizon)
            if lower is None or upper is None:
                unavailable += 1
                continue
            evaluated += 1
            tiered_alerts.extend(_tiered_breaches(
                current, frozen, metric, scope, horizon, "extreme_atr", "极端ATR边界",
                lower, upper, atr14, prior, current_as_of, source,
            ))
    alerts = [dict(item) for item in tiered_alerts if item["trigger_state"] == "close_confirmed"]
    alerts.sort(key=lambda item: (item["horizon_sessions"], item["side"], -abs(item["breach_pct"]), item["symbol"]))
    tiered_alerts.sort(key=lambda item: (item["horizon_sessions"], item["side"], 0 if item["trigger_state"] == "close_confirmed" else 1, -abs(item["breach_pct"]), item["symbol"]))
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
            for layer in ("extreme_atr",)
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
        "note": "首页只报告突破冻结极端ATR边界的记录，不再显示结构位或期限末分位的普通穿越。未出现的标的不代表方向安全；极端ATR突破也不等于立即反转或交易信号。",
    }

