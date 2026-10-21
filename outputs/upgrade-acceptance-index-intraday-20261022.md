# 2026-10-22 日更：三大指数量价与期权地图增量验收

生成时间：2026-10-22（最近完整美股交易日 2026-10-21）；盘中快照：`index-intraday-20261021T135018Z-be2cd1`；日线模型：`radar-20261021-7317f1f3`。

| 检查项 | 结果 | 证据 |
|---|---|---|
| 顶部恰好三张指数卡 | PASS | `site/index.html` 在桌面和390px移动端均检测到3张卡 |
| 原极端ATR预警仍存在 | PASS | `#boundaryAlertPanel` 保留，原渲染函数未移除 |
| 三个指数与成交代理身份分离 | PASS | IXIC/QQQ、SPX/SPY、DJIA/DIA分栏；禁止QQQ→IXIC换算 |
| POC/VAL/VAH/VWAP与横向Profile | PASS | 三卡均有当日5分钟OHLCV估算Profile，固定网格与约70%价值区 |
| 前日冻结及5时段复合窗口 | PASS | 快照包含`today`、`previous`、`composite_5` |
| 期权基础层 | PASS | 日线期权候选区间100/100可用；地图QQQ/SPY/DIA链均抓到；OI日期由供应商未提供，未伪造；Gamma/GEX明确不可用 |
| 条件情景与失效点 | PASS | 三卡均生成上下行情景、两根完成5分钟K线确认规则和失效条件 |
| 不改变原概率/排名 | PASS | `index_intraday`独立字段；日线模型run_id和100股记录未改写 |
| 日线/盘中时间分离 | PASS | 卡片同时显示盘中`generated_at`和`daily_model_as_of=2026-10-21` |
| JSON数据契约 | PASS | `contracts/dashboard.schema.json` 对`index_intraday`及三张卡校验通过 |
| 全池日更 | PASS | 102/102价格可用；5/10/21三周期模型完成；100/100有效预测；基本面理论估值86/100 |
| 常规时段校验 | PASS | 102/102标的完成Yahoo 5分钟常规时段聚合；`observed=102`、`unavailable=0`，其中34条按聚合值修正 |
| 语法与回归测试 | PASS | 90个Python测试、2个盘中地图单元测试、生产数据契约与发布日期检查通过 |
| 浏览器验收 | PASS | 桌面1440px、移动390px；3张地图卡、13行指数矩阵、100股表格，无JS错误、无横向溢出 |

## 限制

- 当前数据源为Yahoo Finance/yfinance，延迟和实时权限未由供应商确认，因此页面不显示“实时”徽章。
- POC/VA/VWAP使用5分钟OHLCV均匀分配估算，不是逐笔成交还原；现金指数没有自身成交量，成交结构只来自明确标注的ETF代理。
- 常规OI的结算日期未被供应商链返回；OI只作为链快照展示，未用于方向概率。Greeks/GEX和Gamma Flip保持不可用。
- 本地日更已完成并保留旧run；远端Pages是否更新需以本次发布工作流的部署状态和线上`run_id`复核为准。
