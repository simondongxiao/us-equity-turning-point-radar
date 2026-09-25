"""Same-session index observations, separate from calibrated model inputs."""
import math
import pandas as pd

INDEX_SPECS = {
    "^SOX": ("SOX · 费城半导体", "price_index"),
    "^GSPC": ("S&P 500 · 标普500", "price_index"),
    "^IXIC": ("Nasdaq Composite · 纳斯达克综合", "price_index"),
    "^NDX": ("Nasdaq-100 · 纳斯达克100", "price_index"),
    "^RUT": ("Russell 2000 · 罗素2000", "price_index"),
    "^DJI": ("Dow Jones · 道琼斯", "price_index"),
    "^VIX": ("VIX · 波动率指数", "volatility_index"),
    "SPY": ("SPY · 标普500 ETF", "etf_proxy"),
    "QQQ": ("QQQ · 纳斯达克100 ETF", "etf_proxy"),
}


def number(value):
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None


def aligned_close(frame, calendar, kind="price_index"):
    column = "adj_close" if kind == "etf_proxy" else "close"
    if frame is None or column not in frame:
        return pd.Series(index=calendar, dtype=float)
    return frame[column].reindex(calendar)  # never fill gaps or use future data


def build_index_context(frames, records, as_of):
    calendar = frames["SPY"].index[frames["SPY"].index <= as_of]
    rows = []
    series = {}
    for symbol, (name, kind) in INDEX_SPECS.items():
        values = aligned_close(frames.get(symbol), calendar, kind)
        series[symbol] = values
        current = number(values.iloc[-1]) if len(values) and calendar[-1] == as_of else None
        changes = {}
        for h in (1, 5, 10, 21):
            window = values.iloc[-h-1:]
            changes[str(h)] = number(window.iloc[-1] / window.iloc[0] - 1) if len(window) == h+1 and window.notna().all() and window.iloc[0] > 0 else None
        rows.append({"symbol": symbol, "name": name, "kind": kind, "as_of": str(as_of.date()),
                     "status": "observed" if current is not None else "unavailable", "level": current,
                     "returns": changes, "unit": "index_points" if kind != "etf_proxy" else "adjusted_usd",
                     "note": "VIX变化不是股票收益或触底概率" if kind == "volatility_index" else "ETF为调整后价格；指数为价格指数，口径不同"})
    for record in records:
        stock = aligned_close(frames.get(record["symbol"]), calendar, "etf_proxy")
        comparisons = []
        for symbol in ("^SOX", "^GSPC", "^NDX", "^RUT"):
            benchmark = series[symbol]
            relative = {}
            for h in (5, 10, 21):
                a, b = stock.iloc[-h-1:], benchmark.iloc[-h-1:]
                relative[str(h)] = number(a.iloc[-1]/a.iloc[0] - b.iloc[-1]/b.iloc[0]) if len(a) == h+1 and a.notna().all() and b.notna().all() and a.iloc[0] > 0 and b.iloc[0] > 0 else None
            comparisons.append({"symbol": symbol, "name": INDEX_SPECS[symbol][0], "relative_returns": relative})
        record["index_context"] = {"as_of": str(as_of.date()), "comparisons": comparisons,
            "model_status": "context_only_not_calibrated", "self_excluded": False,
            "note": "指数可能包含本股，未剔除自身；只作外部市场对照，不替代LOO同行、不重复加权、不改变生产概率。"}
    return {"version": "index-context-v1", "as_of": str(as_of.date()), "rows": rows,
            "model_status": "context_only_not_calibrated", "source": "Yahoo Finance daily index/ETF observations",
            "definition_source": "https://indexes.nasdaq.com/Index/Overview/SOX",
            "note": "SOX使用^SOX原指数，不用SOXX冒充。新增指数仅作同时间观察，未纳入生产概率。"}
