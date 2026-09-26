"""Emit original and v1.1 checklists separately with conservative evidence."""
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'outputs/upgrade-acceptance.md');args=p.parse_args()
    lines=['# 本轮增量验收','',
           'PASS仅表示本轮已验证的具体范围；BLOCKED不代表既有能力已删除。B3保留，新增v2.2仅影子比较。','']
    for line in (ROOT/'references/acceptance.md').read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):lines.extend([line,''])
        if not line.startswith('- '):continue
        status='BLOCKED';reason='本轮证据不足，未宣称完整通过。'
        if any(s in line for s in ('新项目在D:', '原包文件清单无删除','原已','首版100个唯一symbol','首版种子100个唯一')):
            status='PASS';reason='仅修改本雷达，Git保留原实现；种子100只与60/40结构测试通过。证券身份部分仍以原验证记录为准。'
        if any(s in line for s in ('机会↓','数值排序','过滤不重算','分类筛选/全部恢复','存储快捷入口','详情同时保留','手机可操作')):
            status='PASS';reason='现有桌面/手机浏览器回归和15项排序断言通过；新增验证页12组合通过。'
        if 'VIX/IV/OI/Gamma缺失' in line:
            status='PASS';reason='公开缺失与shadow状态，期权未进入新概率拟合。'
        if any(s in line for s in ('动态池历史和幸存者偏差','旧预测不可变','挑战者晋级/回滚规则','runner临时磁盘之外')):
            status='PASS';reason='明确当前池条件偏差；冻结文件冲突拒绝；晋级门槛登记；同仓库Release认证加密台账已配置。'
        if '训练/校准/测试按时间' in line:
            status='BLOCKED';reason='新shadow使用实际成熟日双边purge并通过测试；旧B3的完整同级验证尚未重做，不能以新模型结果替旧模型背书。'
        if any(s in line for s in ('同bar','固定价位触达概率','共同路径概率及区间自洽')):
            status='BLOCKED';reason='现有B3保留。新shadow仅联合事件/条件极值混合验证，尚非完整离散hazard与OHLC共同路径实现。'
        if any(s in line for s in ('网关真实鉴权','池外有效股票完成','单股和批量','错误代码/模糊名称','按周自动调整','首次按近期','每周点时')):
            status='BLOCKED';reason='云端交互任务网关或更广母池周更验收未完成；页面继续明确缺口。'
        if any(s in line for s in ('新存储因子有真实','LOO按发行人','权重和成员点时','SNDK实体','已周更生产池')):
            status='BLOCKED';reason='缺历史成员/业务生效档案；本轮没有将当前分类回填历史。原池与历史预测保留。'
        if 'Pages真实可访问' in line:
            reason='发布完成后按真实run_id另行线上验证；单纯push不计PASS。'
        lines.append(f'- **{status}** {line[2:]} — {reason}')
    lines.extend(['','## 六项升级状态','',
                  '- PASS（工程）预测先冻结、结算另存；训练/校准实际成熟日隔离；加密归档恢复。',
                  '- PASS（已运行）近一年4段影子比较、独立顶底校准、条件价带误差与宽度、次日开盘净持有收益。',
                  '- FAIL（晋级）尚未证明持续优于技术基准，不能宣称70%胜率或误差稳定≤3%。',
                  '- BLOCKED 历史动态股票池及完整发行人级行业层；历史期权链/GEX；完整共同路径验证后的扇形图、归因与压力概率。'])
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(args.output)

if __name__=='__main__':main()
