# 决策一致性审计（v1.2.0 → v1.3.0 前置审计）

审计日期：2026-10-02（北京时间）  
审计对象：生产快照 `radar-20261001-8c90cfbe`，数据截止 2026-10-01，模型版本 `champion-b3-calibrated-low-confidence-v1` / `challenger-exclusive-directional-turn-v1`。  
审计目的：在修改公式和页面语义前，先确认当前机会、风险、方向概率、候选价带与排序字段的真实含义。此文件是变更前基线，不是用阈值或截断修饰异常的补丁。

## 1. 当前生产口径的事实记录

### 1.1 机会值、风险值和排序分

当前 `scripts/radar_engine.py::build_scenario_metric` 的实际计算为：

```text
terminal_j = ref × (1 + historical_terminal_return_j × scale_ratio)
net_j      = terminal_j / ref - 1 - 0.0015
expected   = Σ(w_j × net_j)
sd         = sqrt(Σ(w_j × (net_j - expected)^2))
ESS        = 1 / Σ(w_j²)
mu_lcb     = expected - 1.645 × sd / sqrt(ESS)
D_j        = max(0, 1 - min_price_j / ref)
risk_value = mean(sort(D_j)[top ceil(5% of paths)])
opportunity_value = mu_lcb / max(risk_value, 0.02)
```

`risk_value` 同时被输出为 `es95`，但实现没有计算具有明确尾部概率定义的 Expected Shortfall 置信区间；它是按路径极值回撤的最差 5% 路径均值。当前页面又把 `es95` 直接按收益百分比展示，因此它看起来像“ES95”，但代码中没有 `tail_probability`、`loss_only`、`VaR cutoff` 或 `return_unit` 字段。

当前 `pct_rank` 对全池有限值做横截面百分位排序：

```text
score = 100 × rank / (N - 1)
```

机会分越高仅代表 `opportunity_value` 在当前横截面越高；风险分越高仅代表 `risk_value` 在当前横截面越高。两者不是概率，也不是收益率或亏损率。代码目前没有保留明确的 `score_definition`、`ranking_universe`、`ranking_direction` 和 `score_unit`。

### 1.2 当前页面语义

主页当前列名为“机会分”“风险分”，并在同一单元格中把 `expected_return` / `es95` 按百分比显示。详情页同时展示：

- 边界首达：`p_upfirst / p_downfirst / p_unhit`；
- 互斥方向拐点：`p_bottom_rebound_first / p_top_reversal_first / p_no_directional_turn`；
- 重叠事件：`p_bottom / p_top / p_two_way_wash`；
- 四层价带和候选顶底。

这些字段已经部分区分了“首次触达”“方向性拐点”和“双向洗盘”，但主页仍然把机会/风险横截面排序放在主入口，尚无单独的决策榜，也没有按股票/期限给出唯一的 `阶段主判断`。

## 2. SNDK 当前输出的核对

当前参考价为 1,787.6899。生产快照中 SNDK 的核心字段如下（小数为原始值，页面显示时才转百分比）：

| 期限 | opportunity_value | risk_value / es95 | expected_return | mu_lcb | ESS / 路径数 | 方向性底 / 顶 / 无拐点 | 双向洗盘 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5 日 | 0.0143 | 1.9635 | 0.0619 | 0.0281 | 153.86 / 160 | 0.1743 / 0.2414 / 0.5843 | 0.0355 |
| 10 日 | 0.0584 | 1.9688 | 0.1780 | 0.1150 | 150.54 / 160 | 0.3037 / 0.3623 / 0.3340 | 0.1165 |
| 21 日 | 0.2126 | 1.9688 | 0.5479 | 0.4186 | 150.88 / 160 | 0.3514 / 0.4807 / 0.1679 | 0.2723 |

这解释了现有排序：SNDK 的 5/10/21 日机会分约为 96.97 / 97.98 / 100，风险分三期都是 100。原因不是“未来必然上涨且必然下跌”，而是当前 `mu_lcb` 在全池较高，同时极值回撤分布也在全池最高一档。

但 `risk_value` 超过 1（约 196%）对于普通未杠杆多头简单价格回报的“亏损幅度”不具有可执行的普通价格含义。审计确认：这不是页面小数/百分比换算问题，也不应在 v1.3 中简单 `min(risk, 1)`。根因定位为旧路径的线性距离外推：当前ATR较大时，`ref - (ref - historical_min) × scale` 可小于0。v1.3改用正价格资产的乘法比例外推，并继续将无效风险从风险榜剔除且保留异常原值。

当前 SNDK 5 日结构核心底/顶区都接近参考价，候选底/顶区存在重叠；但代码未提供“已进入候选区”的状态，也未提供候选区触达概率、触达后反转概率及两者联合概率。因此目前页面把“结构价带”“模型极值带”“方向性概率”并列展示，却不能回答“当前是否已经在候选区”“先触达且反转的联合概率是多少”。

## 3. 逐项审计结果

### 3.1 机会值：单位和命名不完整

`opportunity_value` 是无量纲的“下置信收益 / 风险值”比值，但 `risk_value` 在异常时可能不是合法损失率，导致比值既不能稳定解释为收益风险比，也不能当作胜率。`expected_return` 扣除了固定 15bp 成本，成本口径未按 `horizon`、流动性、开盘执行或杠杆资产单独说明。

需要新增：`expected_return_weighted_mean`、`expected_return_median`、`expected_return_p25/p75/p05/p95`、`probability_of_loss`、`expected_shortfall`、`upside_tail_contribution`、`top_5pct_paths_contribution_to_mean`，并标注 `mean_tail_driven`。原始 `opportunity_value`、`risk_value`、`es95` 必须保留用于追溯。

### 3.2 风险 / ES95：实现与名称不一致

当前 `es95` 只是 `risk_value` 的别名，实际为最差 5% 路径的平均极值回撤。它没有校验：

- 是否为普通未杠杆多头简单回报；
- `min_price >= 0` 且 `ref > 0`；
- `adjusted OHLC` 是否同单位、公司行动是否一致；
- 是否有 leveraged ETF、期权、期货或可为负的价差路径；
- 亏损是否相对参考价而非混入收益/差价单位。

因此 v1.3 必须输出 `risk_metric_definition`、`risk_metric_unit`、`risk_metric_valid`、`risk_metric_validation_reason`。无效风险不得进入风险榜；不能用 clamp 掩盖源头。

### 3.3 概率体系：已有三套概率，缺少主判断路由

当前至少存在三类不同事件：

1. `p_upfirst / p_downfirst / p_unhit`：ATR 边界首次触达；
2. `p_bottom_rebound_first / p_top_reversal_first / p_no_directional_turn`：独立训练的互斥方向性拐点；
3. `p_bottom / p_top / p_two_way_wash`：共同历史路径的重叠事件诊断。

第二类适合驱动阶段主判断，第三类不能直接当作顶底概率。现有代码已经在文字中说明这一点，但没有把“使用哪一类概率做决策”的规则编码为字段，也没有用校准概率与基线比较形成 `directional_edge_vs_baseline` 和基于回测阈值的 `probability_margin_top1_top2`。

### 3.4 排序：机会/风险榜和决策榜混在一起

当前横截面只按 `opportunity_value` 或 `risk_value` 排序。它没有使用：方向性概率、方向基线、候选区联合事件、数据质量、校准、条件收益/风险来形成独立决策榜。因此机会榜不能替代阶段底决策，风险榜也不能替代阶段顶警告。

`SNDK` 同时出现在机会与风险榜是当前公式的自然结果：分子 `mu_lcb` 高、极值回撤尾部也高；这并不矛盾，但目前页面缺少分解说明和“高机会 × 高风险”诊断分类。v1.3 需要保留双榜并新增决策榜，不能通过强制互斥把 SNDK 从其中一榜删除。

### 3.5 历史来源 / 样本有效性：有符号筛选但无审计字段

常态 100 股路径在 `build()` 中按 `samples[h][symbol] == symbol` 筛选，生产 SNDK 路径不是直接拼接 WDC 历史。临时观察股票则使用全体 `samples[h]`，属于 peer-transfer / cross-symbol 路径，当前没有在输出中明确区分。

当前输出只有 `scenario_count=160` 与 `effective_scenario_n≈150`，没有：

- `history_source`（own_history / peer_transfer / regime_neighbor）；
- `own_history_sample_count`；
- `peer_transfer_sample_count`；
- `effective_weighted_sample_size`；
- `nominal_path_count`；
- `unique_trade_dates`；
- `unique_regimes`；
- `max_single_path_weight`；
- `top_5_weights_share`。

因此目前不能在页面上证明 SNDK 使用了多少自身历史、多少相似体制样本，也不能识别日期重叠造成的“160 条路径其实来自较少独立交易日”。低 ESS 应降低决策资格，不应篡改原始概率。

### 3.6 候选区：四层价带已存在，但主候选语义未闭环

当前已有：统计包络、价格结构、ATR 波动率带、期权约束、共同路径价带和方向概率。实现中 `±1σ / ±2σ` 是描述性统计包络，不应被称为顶底区；结构核心区是证据，不是概率；模型路径区是条件路径分布，不是下一交易日方向概率。这一分层应保留。

缺口是：主候选底/顶没有统一优先使用“方向性拐点条件区”，也没有距离、触达、条件反转和联合事件字段。需要补充 bottom/top 对称字段，并在已进入候选区时改用“当前已进入候选底/顶部区”的状态，而不是继续把未来触达概率当主信息。

### 3.7 时间期限：不同期限已有不同模型值，但页面缺少跨期限解释

SNDK 的方向性顶概率从 5 日 24.14% 升到 10 日 36.23%、21 日 48.07%，而短期方向性底概率为 17.43% / 30.37% / 35.14%。这是不同观察窗口、不同目标事件和不同校准输出，不是同一个概率随时间机械累加。当前页面没有显式标出“期限之间可能结论不同是正常的”，也没有区分 `horizon_mismatch` 与模型冲突。

## 4. 必须先修的优先级

### P0：数据和单位安全

1. 修复或明确路径价格的非负性、复权 OHLC、一致单位和杠杆资产口径。
2. 增加风险定义/单位/有效性字段；无效风险不进入风险排序。
3. 补齐 expected-return 分布和尾部贡献字段。
4. 将历史来源、名义路径数和 ESS 审计字段落到每个 symbol/horizon。

### P1：决策语义

1. 新增有限枚举 `stage_primary_judgment`。
2. 新增底/顶候选区距离、触达、条件反转、联合概率。
3. 新增 `decision_eligible` 与明确阻塞原因，不修改原始概率。
4. 新增方向决策榜，机会/风险榜保留为独立统计榜。
5. 将页面的 0–100 显示统一改为 `/100`，收益率和概率继续使用各自单位。

### P2：验证和解释

1. 增加 Case A–E 合成测试，覆盖已在底部、触底不确认后到顶、触底确认后新低、持续上行、同窗同时出现顶底。
2. 增加高机会 × 高风险 A–F 诊断分类，并将 SNDK 作为旧/新输出案例。
3. 增加“缺数据/低校准/体制漂移/样本不足/单位异常/期限不匹配”的可解释阻塞消息。
4. 保留指数/ETF 独立自身历史、每日杠杆 ETF 不做线性倍乘、存储分组和既有 100 股池机制。

## 5. 不在本审计阶段做的事

- 不把风险值硬截断到 100% 或 1；
- 不把 `p_bottom` / `p_top` 强行改成和为 100%；
- 不用机会分减风险分生成阶段判断；
- 不把结构共振硬转成方向概率；
- 不因 SNDK 同时进两榜而删除任一榜；
- 不删除原始 `opportunity_value`、`risk_value`、概率和旧回测字段；
- 不改 100 股池、指数/ETF、轮动、存储和临时观察的既有入口。

## 6. 审计结论

当前系统已经具备“边界首达 / 方向性拐点 / 重叠洗盘”三类概念和四层价带雏形，但统计榜、决策榜、风险审计和历史样本审计尚未完全分离。SNDK 同时出现在机会与风险榜是公式层面的可解释现象；真正需要修复的是风险指标的单位有效性、候选区联合事件、决策资格和页面主判断，而不是用显示层阈值消除双榜结果。

下一步按本审计结果实施 v1.3.0：先加数据结构和审计字段，再接入方向主判断与决策榜，最后更新页面、合成测试、SNDK 新旧案例和远端验收。
