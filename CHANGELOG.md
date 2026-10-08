# 变更记录

## 1.5.1-generic-research-group-rotation — 2026-10-08

- 首页固定“Rotation Snapshot 存储”改为全研究组动态轮动：统一计算5/20交易日成员个股等权收益相对SPY，并展示最强/最弱组。
- 每个研究组、每个窗口至少2只可用成员才进入轮动排序；轮动仅作描述，不改写顶底概率、机会/风险分、决策榜、信度或100股名单。
- 周更股票池首页摘要移除MU/SNDK/WDC/STX/NTAP固定排名，改为展示母池、完整人气分、科技成长/非科技覆盖、股份类别缺口与自动调池状态。
- “存储与内存”研究组、业务标签、LOO同行、专属详情和后台shadow/challenger审计全部保留；选择该研究组时仍可查看存储专属轮动。

## 1.2.0-structure-range-layer — 2026-10-01

按“原始统计包络与结构处理”参考表升级候选价带展示，但不改写B3/方向性概率口径：

- 个股详情新增四层价带：实现波动率描述性约±1σ/±2σ、带锚点日期与来源的结构核心区、共同路径模型区、独立校准方向概率。
- 结构水平按ATR/价格距离聚类，保留Fib、均线、摆动点、缺口、AVWAP与成交量分布等证据；共振分只解释核心区，不计入概率。
- 期权快照同时输出约±1σ/±2σ允许范围；期权仍是显示约束，历史准入门槛与B4 BLOCKED治理不变。
- 事件层显式输出`not_ingested`，不把事件缺失默认为中性；统计包络增加5/10/21交易日宽度一致性审计。
- 新增结构/统计包络单元测试；Python 58项、Node排序15项、浏览器14组、交付校验均通过。

## 1.1.3-confidence-calibration — 2026-09-29

指数区标题精简为“指数方向性拐点”，新增SOXL、SOXS、TQQQ、SQQQ。四只ETF按自身复权OHLC独立计算5/10/21交易日方向拐点，不采用指数概率乘以3或多空镜像；详情新增正式基准、每日重置、复利/波动损耗、费用与跳空风险说明。指数区由9项扩展为13项，常态100股和既有股票模型不变。

取消界面上所有指数/个股统一显示“已校准·低可信”的做法。新增逐标的、逐周期证据信度分级，综合历史路径重合、近期样本外多类ECE、相对多数类基线的准确率增益、真实测试条数/交易日数与体制漂移；高确信必须有至少3个百分点的基线增益，信度不改写概率或榜单。

5/10/21交易日按钮改为完整文字，股票表头、指数层和详情随全局周期同步显示当前期限。新增近一年方向拐点校准曲线，按方向性阶段底、阶段顶、无有效拐点分别展示预测均值、实际频率、样本数与完整交易日分块重采样95%区间；无成熟数据时明确空状态。

交付校验新增信度门槛、校准曲线、周期表头与桌面/手机交互测试。B3冠军、方向挑战者身份、100股、存储四只、旧冻结预测和期权B4准入状态均保持不变。

修复公共行情源偶发返回较旧但非空历史时覆盖更新缓存的问题：刷新现在按交易日合并，重叠日采用新下载，同时保留下载缺失的更新已验证交易日，并在来源清单记录`refresh_regressions`。发布时间保护仍拒绝真正较旧的构建。

修复周更审计按构建目录日期而非文件内`base_date`选择母池快照的问题。次日生成但对应前一完整美股交易日的只读母池现在可以被正确选中；未来`base_date`仍被拒绝，未验证的周更选股继续保持BLOCKED。

周更审计进一步读取旧筛选器只读`full_charts.pkl`，按每个历史时点的合格母池计算20/60日美元成交额中位数、每日活跃前300持续比例及截尾后的5日/60日活跃变化，严格使用45%/25%/20%/10%权重并按SEC CIK去重。GOOGL/GOOG与BRK-B/BRK.A按不同股份类别记录，不用另一类别替代流动性；正式分类映射、拆股量价口径和GitHub runner数据入口未完成前不自动换池。

网页新增周更股票池审计摘要，展示同一数据日的母池规模、完整人气分数量、存储候选排名、精确股份类别缺口与自动调池状态；摘要不改写100股、机会/风险分或概率。

按最新界面要求移除工具栏中重复的“存储与内存”“全部存储细分”“全部股票”三个控件。存储四股仍通过研究组下拉选择，DRAM、HBM、NAND、SSD、HDD仍通过业务标签下拉筛选；存储轮动摘要、100股、概率、排序和详情均保持不变。

## 1.1.2-directional-turn — 2026-09-28

在不覆盖B3机会/风险、历史预测和四态影子台账的前提下，新增独立时间切分与校准的互斥方向拐点挑战者：`p_bottom_rebound_first / p_top_reversal_first / p_no_directional_turn`严格合计100%。原`p_bottom / p_top`继续保留为可重叠共同路径事件诊断，不再用于主表阶段顶底排序；同窗顶底事件单独显示为“双向洗盘”。

价带拆分为Price Structure结构价带、ATR期限缩放波动率回退带及期权约束后候选带。期权链缺失时保留结构带并明确显示ATR回退；不再用`—`掩盖已有波动信息，也不把回退带冒充结构确认或期权结果。

新增期权B4历史准入门槛：只有至少252个唯一点时交易日且ATM IV、RV-IV、Put/Call Skew、期限结构覆盖达到80%，才进入GBDT挑战者回测。当前连续历史不足，状态保持`BLOCKED`；Gamma Flip、dealer GEX和Options Flow不从免费当日链补造。

指数层同步升级为互斥方向三分类并保留独立purge校准；新增近一年四折方向拐点walk-forward、数据契约、交付校验、Node排序及桌面/手机浏览器验收。

## 1.1.1-storage-audit — 2026-09-26

保留B3生产模型并修正展示口径：B3阶段顶/底明确为共同历史路径中的场景占比，不再冒充独立校准胜率，也不是下一交易日涨跌预测。

主表新增随1周/2周/约1月切换的“先上冲/先下探/未触达”三分支；个股多周期表同时展示5/10/21交易日首次触达概率，避免数据只藏在详情末尾。

影子Challenger升级至`shadow-purged-joint-v2.3`：新增基于成熟校准路径的5/10/21日扇形图、条件极值带、分组留中位数敏感度和指数收益冲击压力测试。结果仍受当前100股选择偏差影响；敏感度不是SHAP，压力测试不是情景预测。

冻结预测、另行结算、双边成熟日purge、加密Release归档和生产/挑战者隔离继续保留。历史动态成分、完整发行人级行业层、连续历史期权链/GEX及正式任务网关仍按验收表标记BLOCKED。

## 1.1.0-storage — 2026-09-15

在1.0完整包上增量修改。100个股票代码及60/40覆盖桶不变；MU/SNDK/WDC/STX统一为“存储与内存”；其余原基础设施7只改为“算力网络与光通信”；主组与多值标签分离。

新增存储快捷筛选、细分筛选、业务标签、轮动/LOO详情与真实缺数据提示；保留四向排序、周期、非科技、临时输入和原详情研究块。

新增点时LOO参考工具与合成测试、扩展schema/交付校验/浏览器测试；新增存储母池/轮动/降级/回测规范和安全迁移说明。

金融预测引擎、实盘数据、真实历史回测、GitHub部署与网关仍未在本交付包内完成。原规范继续要求首次实施落实这些组件，不能用本次测试冒充上线。
# v1.3.0 · decision consistency and risk audit

- Added pre-change `diagnostics/decision_consistency_audit.md` with formula, unit, ranking, sample-source and SNDK baseline audit.
- Added explicit risk/ES validity, expected-return distribution, tail-contribution, own-history/peer-transfer and ESS fields.
- Added candidate-zone distance/touch/joint-event fields, stage primary judgment, decision eligibility and backtest-derived directional margin threshold.
- Added an independent stage-bottom/stage-top decision board while preserving the opportunity/risk statistical boards and the existing universe/index/ETF/storage/temporary workflows.
- Updated the HTML labels so `/100` scores are not presented as probabilities or buying win rates; invalid ordinary-long tail risk remains visible as an anomaly and is excluded from ranking.
