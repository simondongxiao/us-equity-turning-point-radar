"""Five-step candidate top/bottom range adapter.

This module is deliberately descriptive.  Price structure creates candidate
levels first; option distribution constrains them; skew and event data then
describe asymmetric tail risk.  The layers are shown beside the calibrated
common-path model, but are not fed back into B3 or used to manufacture a
probability.  Option IV is risk-neutral and retrieved at build time.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


HORIZONS = (5, 10, 21)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and math.isfinite(float(value))


def _num(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not ok.any():
        return None
    values, weights = values[ok], weights[ok]
    order = np.argsort(values, kind="mergesort")
    values, weights = values[order], weights[order]
    return float(values[np.searchsorted(np.cumsum(weights), np.sum(weights) / 2, side="left")])


def _clean_option_frame(frame: pd.DataFrame | None, spot: float) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    cols = [c for c in ("strike", "impliedVolatility", "volume", "openInterest") if c in frame.columns]
    if "strike" not in cols or "impliedVolatility" not in cols:
        return pd.DataFrame()
    out = frame[cols].copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    volume = out["volume"] if "volume" in out else pd.Series(0.0, index=out.index)
    open_interest = out["openInterest"] if "openInterest" in out else pd.Series(0.0, index=out.index)
    out["weight"] = volume.fillna(0).clip(lower=0) + open_interest.fillna(0).clip(lower=0) + 1
    out = out[(out.strike > 0) & (out.impliedVolatility > 0) & (out.impliedVolatility < 5)]
    return out[(out.strike >= spot * 0.45) & (out.strike <= spot * 1.75)].copy()


def _iv_near(frame: pd.DataFrame, spot: float, ratio: float, side: str | None = None) -> float | None:
    if frame.empty:
        return None
    x = frame.copy()
    if side == "put":
        x = x[x.strike <= spot]
    elif side == "call":
        x = x[x.strike >= spot]
    if x.empty:
        return None
    x["distance"] = (x.strike / spot - ratio).abs()
    x = x.sort_values("distance", kind="mergesort").head(7)
    return _weighted_median(x.impliedVolatility.to_numpy(float), x.weight.to_numpy(float))


def _choose_expiry(expiries: list[str], as_of: pd.Timestamp, horizon: int) -> str | None:
    if not expiries:
        return None
    target = (as_of + pd.tseries.offsets.BDay(horizon)).date().isoformat()
    future = [e for e in expiries if e >= target]
    return future[0] if future else expiries[-1]


def _rv20(frame: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    close = pd.to_numeric(frame.loc[:as_of, "adj_close"], errors="coerce").dropna()
    if len(close) < 21:
        return None
    return _num(close.pct_change().tail(20).std(ddof=1) * math.sqrt(252))


def _option_snapshot(symbol: str, frame: pd.DataFrame, as_of: pd.Timestamp) -> tuple[str, dict[str, Any], str | None]:
    try:
        spot = float(frame.loc[as_of, "adj_close"] if as_of in frame.index else frame.iloc[-1]["adj_close"])
        ticker = yf.Ticker(symbol)
        expiries = sorted(str(x) for x in ticker.options if str(x) >= as_of.date().isoformat())
        if not expiries:
            return symbol, {"status": "unavailable", "reason": "没有不早于基准日的期权到期日。"}, "no_future_expiry"
        raw: dict[int, dict[str, Any]] = {}
        for horizon in HORIZONS:
            expiry = _choose_expiry(expiries, as_of, horizon)
            if not expiry:
                raw[horizon] = {"status": "unavailable", "reason": "没有匹配到期日。"}
                continue
            chain = ticker.option_chain(expiry)
            calls = _clean_option_frame(chain.calls, spot)
            puts = _clean_option_frame(chain.puts, spot)
            both = pd.concat([calls, puts], ignore_index=True)
            atm = _iv_near(both, spot, 1.0)
            put_iv = _iv_near(puts, spot, 0.90, "put")
            call_iv = _iv_near(calls, spot, 1.10, "call")
            if atm is None:
                raw[horizon] = {"status": "unavailable", "expiry": expiry, "reason": "期权链没有可用IV。"}
                continue
            days = max(1, (pd.Timestamp(expiry).date() - as_of.date()).days)
            move = spot * atm * math.sqrt(days / 365.0)
            raw[horizon] = {
                "status": "observed",
                "expiry": expiry,
                "days_to_expiry": days,
                "atm_iv": atm,
                "put_wing_iv": put_iv,
                "call_wing_iv": call_iv,
                "option_lower_bound": max(0.0, spot - move),
                "option_upper_bound": spot + move,
                "contract_count": int(len(both)),
                "method": "Options Distribution: ATM IV arithmetic one-standard-deviation market-allowed move; constraint only, not an absolute floor or ceiling and not a real-world probability",
            }
        base_iv = raw.get(5, {}).get("atm_iv")
        rv = _rv20(frame, as_of)
        for horizon in HORIZONS:
            item = raw[horizon]
            if item.get("status") != "observed":
                continue
            item["rv20"] = rv
            item["rv_minus_iv"] = rv - item["atm_iv"] if rv is not None else None
            item["put_call_skew"] = (item["put_wing_iv"] - item["call_wing_iv"]) if item.get("put_wing_iv") is not None and item.get("call_wing_iv") is not None else None
            item["term_structure_vs_1w"] = item["atm_iv"] - base_iv if base_iv is not None else None
            item["event_note"] = "当前适配器未核验当日财报/产品发布/宏观事件正文；事件日需在事件确认后重新锚定。"
        return symbol, {"status": "observed", "price_reference": spot, "horizons": raw, "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "Yahoo Finance option_chain via yfinance"}, None
    except Exception as exc:
        return symbol, {"status": "unavailable", "reason": f"期权链读取失败：{type(exc).__name__}"}, str(exc)


def _volume_profile_level(x: pd.DataFrame) -> float | None:
    typical = (pd.to_numeric(x["high"], errors="coerce") + pd.to_numeric(x["low"], errors="coerce") + pd.to_numeric(x["close"], errors="coerce")) / 3
    volume = pd.to_numeric(x.get("volume", pd.Series(0.0, index=x.index)), errors="coerce").fillna(0).clip(lower=0)
    valid = pd.DataFrame({"price": typical, "volume": volume}).dropna()
    valid = valid[valid["price"] > 0]
    if valid.empty or float(valid["price"].max()) <= float(valid["price"].min()):
        return _num(valid["price"].iloc[-1]) if not valid.empty else None
    edges = np.linspace(float(valid["price"].min()), float(valid["price"].max()), 25)
    bins = np.clip(np.digitize(valid["price"].to_numpy(float), edges) - 1, 0, len(edges) - 2)
    totals = np.bincount(bins, weights=valid["volume"].to_numpy(float), minlength=len(edges) - 1)
    idx = int(np.argmax(totals))
    return float((edges[idx] + edges[idx + 1]) / 2)


def _anchored_vwap(x: pd.DataFrame, start_pos: int) -> float | None:
    sub = x.iloc[max(0, start_pos):]
    if sub.empty:
        return None
    typical = (pd.to_numeric(sub["high"], errors="coerce") + pd.to_numeric(sub["low"], errors="coerce") + pd.to_numeric(sub["close"], errors="coerce")) / 3
    volume = pd.to_numeric(sub.get("volume", pd.Series(0.0, index=sub.index)), errors="coerce").fillna(0).clip(lower=0)
    total = float(volume.sum())
    return float((typical * volume).sum() / total) if total > 0 else None


def _technical_layer(frame: pd.DataFrame, as_of: pd.Timestamp, horizon: int) -> dict[str, Any]:
    x = frame.loc[:as_of].copy()
    raw_close = pd.to_numeric(x.get("close"), errors="coerce")
    adjusted_close = pd.to_numeric(x.get("adj_close"), errors="coerce")
    adjustment = (adjusted_close / raw_close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    # Keep OHLC, gaps, AVWAP and volume profile in the same split-adjusted
    # price unit as adj_close. This avoids treating a share-class split as a
    # real gap while preserving the issuer-specific history already supplied.
    x["open"] = pd.to_numeric(x["open"], errors="coerce") * adjustment
    x["high"] = pd.to_numeric(x["high"], errors="coerce") * adjustment
    x["low"] = pd.to_numeric(x["low"], errors="coerce") * adjustment
    x["close"] = adjusted_close
    close = pd.to_numeric(x["adj_close"], errors="coerce").dropna()
    if len(close) < 120:
        return {"status": "unavailable", "reason": "历史日线不足以形成均线与确认结构。"}
    spot = float(close.iloc[-1])
    high, low = pd.to_numeric(x["high"], errors="coerce"), pd.to_numeric(x["low"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if _finite(tr.rolling(14).mean().iloc[-1]) else spot * 0.03
    tail = x.tail(252)
    rolling_high, rolling_low = float(tail.high.max()), float(tail.low.min())
    fib = [(f"Fib {level:.1%}", rolling_low + (rolling_high - rolling_low) * level) for level in (0.236, 0.382, 0.5, 0.618, 0.786)]
    mas = [(f"SMA{n}", float(close.rolling(n).mean().iloc[-1])) for n in (20, 50, 100, 200) if len(close) >= n and _finite(close.rolling(n).mean().iloc[-1])]
    # A swing is only used after two later observations exist; this avoids a
    # look-ahead ZigZag endpoint while still exposing an auditable price label.
    lows = low.shift(2).rolling(5).min()
    highs = high.shift(2).rolling(5).max()
    swing_lows = [("前低/确认摆动低", float(low.shift(2).iloc[i]), i) for i in range(max(0, len(low) - 252), len(low)) if _finite(low.shift(2).iloc[i]) and _finite(lows.iloc[i]) and abs(float(low.shift(2).iloc[i]) - float(lows.iloc[i])) < max(0.01, atr * 0.02)]
    swing_highs = [("前高/确认摆动高", float(high.shift(2).iloc[i]), i) for i in range(max(0, len(high) - 252), len(high)) if _finite(high.shift(2).iloc[i]) and _finite(highs.iloc[i]) and abs(float(high.shift(2).iloc[i]) - float(highs.iloc[i])) < max(0.01, atr * 0.02)]
    last_low_pos = swing_lows[-1][2] if swing_lows else max(0, len(x) - 60)
    last_high_pos = swing_highs[-1][2] if swing_highs else max(0, len(x) - 60)
    volume_profile = _volume_profile_level(tail)
    avwap_low = _anchored_vwap(x, last_low_pos)
    avwap_high = _anchored_vwap(x, last_high_pos)
    candidates_low = fib + mas + [("Volume Profile POC", volume_profile), ("AVWAP(前低锚点)", avwap_low)] + [(label, value) for label, value, _ in swing_lows[-12:]]
    candidates_high = fib + mas + [("Volume Profile POC", volume_profile), ("AVWAP(前高锚点)", avwap_high)] + [(label, value) for label, value, _ in swing_highs[-12:]]
    previous_close = pd.to_numeric(x["close"], errors="coerce").shift(1)
    opens = pd.to_numeric(x["open"], errors="coerce")
    gaps = []
    for i in range(max(1, len(x) - 252), len(x)):
        if not (_finite(previous_close.iloc[i]) and _finite(opens.iloc[i])):
            continue
        gap_pct = float(opens.iloc[i] / previous_close.iloc[i] - 1)
        if abs(gap_pct) >= 0.02:
            level = float((opens.iloc[i] + previous_close.iloc[i]) / 2)
            gaps.append(("Gap上方/下方密集区", level, gap_pct))
    candidates_low += [(f"{label} {gap_pct:+.1%}", level) for label, level, gap_pct in gaps if level <= spot]
    candidates_high += [(f"{label} {gap_pct:+.1%}", level) for label, level, gap_pct in gaps if level >= spot]
    candidates_low = [(label, value) for label, value in candidates_low if _finite(value)]
    candidates_high = [(label, value) for label, value in candidates_high if _finite(value)]
    below = [(label, value) for label, value in candidates_low if 0 < value <= spot]
    above = [(label, value) for label, value in candidates_high if value >= spot]
    below.sort(key=lambda item: abs(item[1] - spot)); above.sort(key=lambda item: abs(item[1] - spot))
    support = below[:3] or [("ATR参考支撑", max(0.0, spot - atr))]
    resistance = above[:3] or [("ATR参考阻力", spot + atr)]
    support_values = [v for _, v in support]
    resistance_values = [v for _, v in resistance]
    sma20 = next((value for label, value in mas if label == "SMA20"), None)
    sma50 = next((value for label, value in mas if label == "SMA50"), None)
    trend = "上行结构" if sma20 is not None and sma50 is not None and sma20 >= sma50 and close.iloc[-1] >= sma20 else "下行/修复结构" if sma20 is not None and sma50 is not None and sma20 < sma50 else "趋势数据不足"
    support_band = [max(0.0, min(support_values) - atr * 0.12), max(support_values) + atr * 0.12]
    resistance_band = [max(0.0, min(resistance_values) - atr * 0.12), max(resistance_values) + atr * 0.12]
    return {
        "status": "observed",
        "support_band": support_band,
        "resistance_band": resistance_band,
        "support_levels": [{"label": label, "price": value} for label, value in support],
        "resistance_levels": [{"label": label, "price": value} for label, value in resistance],
        "front_swing_low": _num(swing_lows[-1][1]) if swing_lows else None,
        "front_swing_high": _num(swing_highs[-1][1]) if swing_highs else None,
        "volume_profile_poc": volume_profile,
        "avwap_from_swing_low": avwap_low,
        "avwap_from_swing_high": avwap_high,
        "gap_levels_observed": len(gaps),
        "trend_structure": trend,
        "atr14": atr,
        "lookback_sessions": int(len(tail)),
        "method": "Price Structure: prior high/low + Volume Profile POC + AVWAP + gap levels + SMA20/50/100/200 + rolling 252-session Fib + trend structure; candidates are generated before options are applied",
        "bottom_confirmation_condition": f"候选底部仅在收盘重新站上支撑候选上沿 {support_band[1]:.2f} 并连续保持时确认；跳空/事件冲击可使其失效。",
        "top_confirmation_condition": f"候选顶部仅在收盘跌回阻力候选下沿 {resistance_band[0]:.2f} 并连续保持时确认；跳空/事件冲击可使其失效。",
    }


def _constrain_candidate(structure: dict[str, Any], option: dict[str, Any], side: str) -> dict[str, Any]:
    key = "support_band" if side == "bottom" else "resistance_band"
    technical_band = structure.get(key)
    option_band = [option.get("option_lower_bound"), option.get("option_upper_bound")] if option.get("status") == "observed" else None
    if not (isinstance(technical_band, list) and len(technical_band) == 2 and isinstance(option_band, list) and all(_finite(v) for v in technical_band + option_band)):
        return {"status": "unavailable", "band": None, "note": "结构候选或期权分布缺失，未形成约束后的候选区域。"}
    lo, hi = max(technical_band[0], option_band[0]), min(technical_band[1], option_band[1])
    if lo <= hi:
        return {"status": "converged", "band": [lo, hi], "technical_band": technical_band, "option_band": option_band, "in_option_range": True, "note": "技术候选与期权允许波动区间有重叠；期权只作约束，不代表绝对顶底。"}
    return {"status": "outside_option_range", "band": None, "technical_band": technical_band, "option_band": option_band, "in_option_range": False, "note": "技术候选未落入当前期权允许波动区间；不强行拼接为候选顶/底。"}


def build_potential_ranges(frames: dict[str, pd.DataFrame], symbols: list[str], as_of: pd.Timestamp) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    option_results: dict[str, dict[str, Any]] = {}
    work = [(symbol, frames[symbol], as_of) for symbol in symbols if symbol in frames]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_option_snapshot, *args) for args in work]
        for future in as_completed(futures):
            symbol, snapshot, error = future.result()
            option_results[symbol] = snapshot
            if error:
                errors[symbol] = error
    for symbol in symbols:
        frame = frames.get(symbol)
        option_snapshot = option_results.get(symbol, {"status": "unavailable", "reason": "没有价格记录。"})
        horizons: dict[str, Any] = {}
        for horizon in HORIZONS:
            option = (option_snapshot.get("horizons") or {}).get(horizon, {"status": "unavailable", "reason": option_snapshot.get("reason", "期权层缺失")})
            technical = _technical_layer(frame, as_of, horizon) if frame is not None else {"status": "unavailable", "reason": "没有价格记录。"}
            layer3 = {k: option.get(k) for k in ("rv20", "atm_iv", "rv_minus_iv", "put_call_skew", "term_structure_vs_1w", "event_note") if k in option}
            layer3["event_catalyst"] = {"status": "unavailable", "items": [], "note": "本次构建未抓取可审计的财报、产品发布或宏观事件正文；不把缺失当作中性，也不制造事件概率。"}
            bottom_candidate = _constrain_candidate(technical, option, "bottom")
            top_candidate = _constrain_candidate(technical, option, "top")
            horizons[str(horizon)] = {
                "status": "observed" if option.get("status") == "observed" and technical.get("status") == "observed" else "partial",
                "as_of": as_of.strftime("%Y-%m-%d"),
                "target_date": (as_of + pd.tseries.offsets.BDay(horizon)).strftime("%Y-%m-%d"),
                "layer1_price_structure": technical,
                "layer2_options_distribution": option,
                "layer3_skew_event": layer3,
                "candidate_bottom": bottom_candidate,
                "candidate_top": top_candidate,
                "note": "Price Structure先生成候选位；Options Distribution只约束合理波动区间；Skew/Put-Call描述尾部不对称；Event/Catalyst缺失时不补假信息。期权不代表绝对底部或顶部。",
            }
        results[symbol] = {"status": option_snapshot.get("status", "unavailable"), "horizons": horizons, "retrieved_at": option_snapshot.get("retrieved_at"), "source": option_snapshot.get("source", "Yahoo Finance option_chain via yfinance")}
    return results, {"status": "observed" if option_results else "unavailable", "requested_symbols": len(symbols), "observed_symbols": sum(1 for x in option_results.values() if x.get("status") == "observed"), "errors": errors, "retrieved_at": datetime.now(timezone.utc).isoformat(), "note": "期权链为构建时快照；只约束Price Structure候选区间，不代表绝对顶/底；未取得的标的明确显示缺失，不用模型价带补假IV。"}
