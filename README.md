# 美股阶段顶底概率雷达｜Codex Skill交付包

本包交付：一个新的长期Skill、100只初始观察种子、完整模型/回测/前端与安全部署规范、可运行HTML交互模板、模板渲染脚本及接口验收脚本。名称：`us-equity-turning-point-radar`。

**边界：没有已训练的金融预测引擎，没有实时100股价带，没有已完成的历史策略回测，也没有本次已发布的GitHub网站或任务网关。** 模板的数据为空值且明确标记未校准；排序逻辑有独立的合成测试。执行Skill的首次构建任务是将这些规范落实到真实可运行项目，然后在可用数据和授权下部署并验收。

## 使用
在Codex打开`D:\codex`。将本包作为附件提供，或解压到工作区中的临时导入目录。复制`BOOTSTRAP_PROMPT.txt`执行；Codex须先读SKILL与全部参考规范，再创建新项目，不能覆盖旧Skill或旧行情网页。

正式完成后日常可说：“按美股阶段顶底雷达做今天更新”；“更新本周人气股池”；“分析MU的阶段顶底并更新网页”。网站输入池外股票通过真实已部署网关执行；不是静态页面自带算力。

## 本地检查本包
`python scripts/render_preview.py`生成`preview.html`，可离线打开查看100股观察种子和交互（所有金融数字保持空值）。
`python scripts/validate_delivery.py --seed assets/universe_seed.csv`检查股票池基础约束。
`node tests/sort.test.cjs`检查四种排序、空值置底和数值排序。

`preview.html`不是实盘网站，不包含概率、价带或实时数据。首次部署后才会由真实引擎填充同一数据契约。

## 文件
SKILL.md为运行编排；references含模型、股票池、页面/网关、回测迭代和验收；assets含100股种子和HTML模板；contracts为结果结构；scripts为渲染/验收工具；tests仅为代码行为测试。

初始60/40是研究覆盖分组，不是正式行业分类。种子是观察对象，不是买入建议，也不是已经统计验证的实时人气100强。数据源与限制见references/sources.md。

## v1.1存储增强
本版是原包的完整增量升级，Skill名称不变。新增加“存储与内存”主组（初始MU/SNDK/WDC/STX）、DRAM/HBM、NAND/SSD、HDD筛选，保留100股与原全部研究/回测/安全要求。`references/storage-memory.md`定义轮动与LOO，`references/migration-v1.1.md`兼容首次安装及已有项目升级。

本次只交付规范、元数据、前端和经合成测试的辅助函数，不包含已训练存储概率模型。离线HTML可验证筛选和详情，任务网关仍未部署。新轮动模块对预测质量的增益仍待真实回测。

附加检查：`python -m unittest discover -s tests -p "test_*.py" -v`；`python tests/browser_preview.py`（需Playwright/Chromium）；`python scripts/verify_package.py`。验证明细见`tests/validation-report.json`和`VALIDATION_REPORT.md`。

## 首次真实构建状态（2026-09-15）

已在本项目原地补齐真实公开日线适配、时间切分B3模型与独立校准、共同历史路径场景集、SQLite追加台账、周更覆盖审计、静态Pages构建和标准库鉴权任务网关。最新本地快照写入`site/index.html`，运行数据保存于`outputs/`（不提交原始行情）。

模型状态为`calibrated_low_confidence`：五年Yahoo Finance日线可用于B3时间检验，存储LOO/细分特征已计算但仍是shadow challenger；由于taxonomy生效日为2026-09-15，没有成熟点时样本外窗口，因此不宣称存储因子带来精度提升。GitHub Pages和外部HTTPS网关仍需实际远端配置后才能标记上线。
