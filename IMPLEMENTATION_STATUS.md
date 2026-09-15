# v1.1 实施状态

最后本地构建：`2026-09-15`

- 最新 run：见 `outputs/dashboard-radar-*.json` 与 `site/data.json`
- 数据截止：2026-09-14（纽约市场日线收盘）
- 常态池：100；科技与成长60；非科技40
- 可用价格记录：100/100；有效低可信预测：100/100
- 母池可见规模：5509；实时人气/资格尚未由本雷达独立复核，未据此强行换池
- 存储组：MU、SNDK、WDC、STX；均按SEC发行人ID完成宽存储LOO；细分样本按proxy/no_peers规则降级
- B3：已用真实历史训练/独立时间校准并生成测试指标；不等于盈利证明
- 存储增量因子：BLOCKED/shadow-only，未套用旧校准器
- 本地网关：已完成鉴权、持久任务、池内MU和池外IBM链路测试；公开HTTPS部署未配置
- Pages：已发布并验证 https://simondongxiao.github.io/us-equity-turning-point-radar/；成功workflow `34957901068`，线上run_id为该次Actions重新生成的 `radar-20260914-29dd2048`
- 外部HTTPS鉴权网关：BLOCKED；仓库提供标准库本地网关实现，但当前没有可用的云端OAuth/OIDC、持久对象存储或网关域名配置，Pages输入按钮保持诚实提示，不开放匿名收费任务
