# 美股阶段顶底概率雷达｜Codex Skill交付包

名称：`us-equity-turning-point-radar`；版本：`1.1.3-confidence-calibration`。本包是同一个Skill的完整增量版本，包含100股种子、模型与回测规范、真实计算引擎源码、冻结台账逻辑、HTML前端、GitHub Pages工作流和测试。

当前生产网站为 https://simondongxiao.github.io/us-equity-turning-point-radar/ 。打包文件不包含行情缓存、冻结预测、SQLite、密钥或GitHub凭证；安装后必须在实际项目中恢复自己的状态和授权。生产B3保持低可信，影子Challenger未晋级；正式HTTPS单股任务网关和动态周更仍为BLOCKED，不能因网页已部署而宣称完成。

## 使用
在Codex打开`D:\codex`。将本包解压后，把Skill安装到当前支持的Skills目录；本机约定为`D:\codex\skills\us-equity-turning-point-radar`。首次构建或升级前完整读取`SKILL.md`和`references`，先检查同名项目状态，再决定首次构建或原地升级，不能覆盖旧Skill或旧行情网页。

日常可说：“按美股阶段顶底雷达做今天更新”；“更新本周人气股池”；“分析MU的阶段顶底并更新网页”。网站只有在真实鉴权网关已部署时才能运行池外股票；当前Pages会诚实显示网关未部署。

## 本地检查本包
`python scripts/render_preview.py`生成`preview.html`，可离线查看100股种子和交互；预览金融数字为空值。
`python scripts/validate_delivery.py --seed assets/universe_seed.csv`检查股票池基础约束。
`python -m unittest discover -s tests -p "test*.py"`运行Python测试。
`node tests/sort.test.cjs`检查排序、空值置底和数值排序。
`python tests/browser_preview.py`运行桌面/手机界面回归。

`preview.html`不是实盘网站，不包含概率、价带或实时数据。生产构建由`radar_engine.py`、`audit_upgrade.py`和渲染/验证脚本生成，不能用预览测试冒充金融回测或上线。

## 文件
`SKILL.md`为运行编排；`references`含模型、股票池、页面/网关、回测迭代和验收；`assets`含100股种子和HTML模板；`contracts`为结果结构；`scripts`含引擎、审计、冻结、渲染与部署辅助；`tests`为工程行为测试。

初始60/40是研究覆盖分组，不是正式行业分类。种子是观察对象，不是买入建议，也不是已经统计验证的实时人气100强。数据源与限制见references/sources.md。

## v1.1存储增强
本版是原包的完整增量升级，Skill名称不变。新增加“存储与内存”主组（初始MU/SNDK/WDC/STX）、DRAM/HBM、NAND/SSD、HDD筛选，保留100股与原全部研究/回测/安全要求。`references/storage-memory.md`定义轮动与LOO，`references/migration-v1.1.md`兼容首次安装及已有项目升级。

分类、筛选与存储轮动已进入生产页面；存储增量因子仍为shadow/challenger，没有套用旧校准器，也未证明提升预测质量。

## v1.1.1审计增强

B3首次触达`先上冲/先下探/未触达`与阶段顶底场景占比分开展示。影子层新增非对称独立顶底、条件极值带、成熟路径扇形图、分组敏感度和指数压力测试；历史动态成员、板块点时成员及连续期权/GEX数据缺口继续明确标为BLOCKED。

## v1.1.2方向拐点增强

主表的方向性阶段底、方向性阶段顶与无有效拐点改为同一目标空间的互斥三分类，三项严格合计100%；同窗先下探后上冲或先上冲后下探仍允许发生，但只在“双向洗盘”诊断中展示，不再冒充两个可同时偏高的方向决策概率。B3机会/风险和首次触达口径没有被覆盖。

潜在价带分为结构价带与ATR波动率回退带；结构证据缺失时仍显示波动率支持/阻力带，而不是直接输出空值。期权IV、期限结构、Put/Call偏斜与GEX只有在连续、带时间戳、覆盖充分的历史快照通过准入后才允许进入新挑战者训练；当前历史覆盖不足时保持`BLOCKED`，不会把当日快照回填成历史特征。

## v1.1.3信度与校准呈现

指数与个股不再统一挂“已校准·低可信”。每个标的、每个周期按历史路径重合、近期样本外校准、相对多数类基线增益和真实日期支持分为高确信、中确信或低可信/无明显偏向；信度不改变概率、机会/风险分或排序。首页明确显示5/10/21交易日全局周期，并增加方向性阶段底/顶/无有效拐点的样本外概率校准曲线与日期分块区间。
