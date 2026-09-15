# 实施参考来源
以下是规范参考，不是当前100只股票人气/价格/模型有效性的证明。访问核对日期：2026-09-15。

- OpenAI：Skill由SKILL.md、可选脚本与引用文件组成。https://developers.openai.com/codex/skills/
- GitHub Pages：静态站点托管。https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- GitHub工作流触发接口及权限。https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
- GitHub计划与其他触发事件、运行限制。https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- SEC证券代码/发行人主表（不是人气榜）。https://www.sec.gov/files/company_tickers.json
- Nasdaq的成交股数与美元成交额活跃度页面（本次抓取具体行情不可用）。https://www.nasdaq.com/market-activity/most-active
- Cboe期权市场统计（不是完整个股历史期权链）。https://www.cboe.com/markets/us/options/market-statistics/daily
- scikit-learn概率校准：独立校准集、可靠性图和评分解释。https://scikit-learn.org/stable/modules/calibration.html
- scikit-learn交叉验证：时间结构与测试集污染。https://scikit-learn.org/stable/modules/cross_validation.html
- Distributional Robust Kelly Gambling：分布不确定性下的凯利。https://web.stanford.edu/~boyd/papers/robust_kelly.html

本包没有从这些网站取得100股完整历史行情/期权链，也没有声称已验证当前100只全部符合实时入池阈值。首次执行必须调用实际可用授权数据源并保存来源、时间及覆盖报告。

## v1.1存储业务分类补充来源
以下只支持业务标签与分拆核查，不证明人气排名、价格预测或投资价值。初次在线运行应复核最新变化。
- MU：Micron Powers AI Everywhere at COMPUTEX 2026。https://investors.micron.com/news/press-release/2026/Micron-Powers-AI-Everywhere-at-COMPUTEX-2026/default.aspx
- SNDK：Sandisk Showcases NAND Innovation for the Era of AI Inference at FMS 2026。https://www.sandisk.com/company/newsroom/press-releases/2026/sandisk-nand-innovation-for-era-of-ai-inference-at-fms-2026
- WDC：Western Digital Completes Planned Company Separation。https://www.westerndigital.com/company/newsroom/press-releases/2025/2025-02-24-western-digital-completes-planned-company-separation
- STX：Seagate Delivers Industry’s Highest Capacity Hard Drives with Next-Generation Mozaic 4+。https://investors.seagate.com/news/news-details/2026/Seagate-Delivers-Industrys-Highest-Capacity-Hard-Drives-with-Next-Generation-Mozaic-4/default.aspx

本次只使用页面的业务/产品/分拆信息，未将网页业绩数字写入行情或预测。
