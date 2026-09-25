"""Freeze an observed public run locally and report conservative acceptance status.

This audits published output; it does not train, settle forecasts or certify alpha.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://simondongxiao.github.io/us-equity-turning-point-radar/"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-run")
    args = parser.parse_args()
    response = requests.get(URL + "data.json", params={"audit": datetime.now(timezone.utc).isoformat()}, timeout=60)
    response.raise_for_status()
    data = response.json()
    run_id = data["run_id"]
    if args.expected_run and run_id != args.expected_run:
        raise ValueError(f"Live run {run_id} differs from deployed artifact {args.expected_run}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
        raise ValueError("Invalid run ID")
    folder = ROOT / "outputs" / "published-runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    snapshot = folder / "dashboard.json"
    if snapshot.exists():
        if snapshot.read_bytes() != canonical:
            raise ValueError("Published run changed under same run_id; refusing overwrite")
    else:
        with snapshot.open("xb") as fh:
            fh.write(canonical)
    source = data.get("source_manifest", {})
    dates = source.get("latest_complete_date_by_symbol", {})
    latest = max(dates.values()) if dates else None
    lagging = {symbol: date for symbol, date in dates.items() if date < latest} if latest else {}
    records = data["records"]
    regular = len(records)
    valid = {str(h): sum(r.get("metrics", {}).get(str(h), {}).get("status") in {"calibrated", "calibrated_low_confidence"} for r in records) for h in (5, 10, 21)}
    lines = [
        f"# 日更验收：{run_id}", "",
        f"- 在线地址：{URL}",
        f"- 构建时间：{data.get('generated_at')}；模型基准日：{data['as_of']}",
        f"- 来源完整日线截止：{source.get('complete_session_cutoff_ny')}；最新单股日期：{latest}",
        f"- 股票池：{regular}；有效预测（5/10/21日）：{valid}",
        f"- 模型：{data.get('model_version')}；存储因子：shadow / BLOCKED",
        f"- 归档SHA256：{hashlib.sha256(canonical).hexdigest()}",
        f"- 拖后共同日期的成员：{json.dumps(lagging, ensure_ascii=False)}",
        "- 单股网关：BLOCKED；每周动态调池：BLOCKED；云端完整持久台账：BLOCKED。",
        "- 本次新增云端派生结果留档90天，本地归档不自动过期；不等于完整模型/任务持久化。",
        "- 当前股池条件回测有选择/幸存者偏差；分类准确率不等于净交易胜率。",
        "", "## 近一年成熟历史评估", "",
    ]
    horizons = data.get("backtest", {}).get("one_year_walk_forward", {}).get("horizons", {})
    for horizon, metric in horizons.items():
        values = {key: metric.get(key) for key in ("classification_accuracy", "bottom_distance_within_3pct", "top_distance_within_3pct", "matured_classification_n", "band_checkpoint_n")}
        lines.append(f"- {horizon}交易日：{json.dumps(values, ensure_ascii=False)}")
    lines += ["", "## 原清单及v1.1增量清单", "", "PASS仅用于本次有证据的项目；复合条目有任一部分未验证则保留BLOCKED。", ""]
    # Conservative mapping: don't inherit previous broad PASS claims.
    verified_prefixes = (
        "新项目在D:", "真实数据、合成测试", "机会/风险/人气分开",
        "VIX/IV/OI/Gamma缺失", "100股数量、可比较预测数量",
        "数值排序、空值永远置底", "过滤不重算100股分位",
        "过滤不重算全池分位", "原包文件清单无删除",
        "首版100个唯一symbol不变", "存储快捷入口显示四只",
        "分类筛选/全部恢复", "详情同时保留原研究块",
        "新存储因子有真实同窗口消融",
    )
    for line in (ROOT / "references" / "acceptance.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and line not in {"## 最终回报格式"}:
            lines += ["", "### " + line[3:], ""]
        elif line.startswith("- "):
            item = line[2:]
            status = "PASS" if item.startswith(verified_prefixes) else "BLOCKED"
            if item.startswith("Pages真实可访问") and args.expected_run == run_id:
                status = "PASS"
            # Factor validation itself remains blocked, even if status is disclosed.
            if item.startswith("新存储因子"):
                status = "BLOCKED"
            lines.append(f"- **{status}** — {item}")
    lines += ["", "## 继续推进顺序", "",
        "1. 排查共同日期滞后，补齐按交易所日历判断的时效门槛。",
        "2. 持久化完整台账/模型并逐日结算冻结预测；按净交易胜率、极值误差分别验收。",
        "3. 核验母池60日成交额/持续性历史，接入点时周更与存储候选覆盖。",
        "4. 在成熟样本上验证存储LOO增量与独立顶/底校准；未过门槛不晋级。",
        "5. 配置HTTPS鉴权网关与持久任务服务，完成真实池外股链路。", ""]
    report = folder / "acceptance.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "as_of": data["as_of"], "valid": valid, "latest": latest, "lagging": lagging, "report": str(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
