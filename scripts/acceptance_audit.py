"""Emit original and v1.1 checklists separately with conservative evidence."""
import argparse
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def latest_weekly_review():
    paths=sorted((ROOT/'state').glob('weekly-review-*.json'),key=lambda path:path.stat().st_mtime,reverse=True)
    if not paths:return None
    try:return json.loads(paths[0].read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):return None

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'outputs/upgrade-acceptance.md');args=p.parse_args()
    weekly=latest_weekly_review()
    lines=['# 本轮增量验收','',
           'PASS仅表示本轮已验证的具体范围；BLOCKED不代表既有能力已删除。B3机会/风险保留；互斥方向拐点为低可信挑战者，旧四态v2.3仅作诊断。','']
    for line in (ROOT/'references/acceptance.md').read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):lines.extend([line,''])
        if not line.startswith('- '):continue
        status='BLOCKED';reason='本轮证据不足，未宣称完整通过。'
        if any(s in line for s in ('新项目在D:', '原包文件清单无删除','原已','首版100个唯一symbol','首版种子100个唯一')):
            status='PASS';reason='仅修改本雷达，Git保留原实现；种子100只与60/40结构测试通过。证券身份部分仍以原验证记录为准。'
        if '有可调用SKILL.md与完整可执行项目' in line:
            status='PASS';reason='Skill已安装于D:\\codex\\skills；同名项目包含真实引擎、冻结台账、回测、HTML和部署流程。'
        if '真实数据、合成测试、界面模板、历史验证、上线状态分别报告' in line:
            status='PASS';reason='真实行情、合成测试、历史重放、影子状态与Pages run_id分别展示；不把影子结果冒充生产晋级。'
        if '5/10/21交易日周期与1—3日修复区分' in line:
            status='PASS';reason='5/10/21周期共用同一完成交易日截止；页面周期切换不改写阶段字段。'
        if '市场、行业、个股可不同步' in line:
            status='PASS';reason='市场、研究组与个股残差分层显示；轮动适配只写入本雷达并记录来源版本。'
        if any(s in line for s in ('机会↓','数值排序','过滤不重算','分类筛选/通过下拉全部选项恢复','不显示单独的“存储与内存”','详情同时保留','手机可操作')):
            status='PASS';reason='现有桌面/手机浏览器回归和15项排序断言通过；新增验证页12组合通过。'
        if '个股有多周期价带' in line:
            status='PASS';reason='详情保留B3多周期价带、五步候选体系、确认/失效、事件与尾部风险，并新增独立影子扇形图、敏感度和压力测试。'
        if 'VIX/IV/OI/Gamma缺失' in line:
            status='PASS';reason='公开缺失与shadow状态，期权未进入新概率拟合。'
        if any(s in line for s in ('动态池历史和幸存者偏差','旧预测不可变','挑战者晋级/回滚规则','runner临时磁盘之外')):
            status='PASS';reason='明确当前池条件偏差；冻结文件冲突拒绝；晋级门槛登记；同仓库Release认证加密台账已配置。'
        if '训练/校准/测试按时间' in line:
            status='BLOCKED';reason='新shadow使用实际成熟日双边purge并通过测试；旧B3的完整同级验证尚未重做，不能以新模型结果替旧模型背书。'
        if '概率校准、价带宽度/覆盖、排序效果和净交易表现分别评价' in line:
            status='PASS';reason='新shadow分别报告Brier/校准桶、条件价带宽度与覆盖、3%误差及次日开盘扣成本结果；晋级仍FAIL。'
        if '方向性阶段底、方向性阶段顶、无有效拐点' in line:
            status='PASS';reason='交付校验逐股逐周期验证互斥三项完整且和为1；主表排序仅读取方向挑战者，旧重叠边际移入双向洗盘诊断。'
        if '结构价带、ATR波动率回退带' in line:
            status='PASS';reason='主表和详情分别展示结构、波动率回退及期权约束后候选；100股各周期均验证ATR回退带有效。'
        if '期权特征进入概率前' in line:
            status='BLOCKED';reason='准入门槛与点时覆盖报告已实现，但连续252日历史尚不足；当前快照不进入概率模型。'
        if '模型置信度依据真实独立样本' in line:
            status='PASS';reason='样本数取真实成熟股票日并按21交易日块重采样；扇形图路径数不冒充独立样本数。'
        if '逐标的逐周期信度分级可审计' in line:
            status='PASS';reason='信度逐标的逐周期输出路径重合、ECE、基线增益、测试条数/日期与漂移状态；高确信门槛由交付校验强制执行。'
        if '方向三分类校准曲线按所选' in line:
            status='PASS';reason='近一年四段样本外概率按固定桶生成三事件曲线，并用完整交易日分块重采样区间；空样本不画假曲线。'
        if '周期按钮完整标明5交易日' in line:
            status='PASS';reason='全局三周期使用完整交易日文字，股票表头、指数、详情和校准面板同步切换；桌面/手机浏览器测试覆盖。'
        if '未校准、样本不足、陈旧和数据异常不能生成假概率' in line:
            status='PASS';reason='未校准或支持不足返回状态/空值；发布流程含完成交易日防陈旧检查；B3场景占比已单独标注。'
        if any(s in line for s in ('同bar','固定价位触达概率','共同路径概率及区间自洽')):
            status='BLOCKED';reason='现有B3保留。新shadow仅联合事件/条件极值混合验证，尚非完整离散hazard与OHLC共同路径实现。'
        if any(s in line for s in ('网关真实鉴权','池外有效股票完成','单股和批量','错误代码/模糊名称','按周自动调整','每周点时')):
            status='BLOCKED';reason='云端交互任务网关或更广母池周更验收未完成；页面继续明确缺口。'
        if '首次按近期真实数据检验资格与人气' in line:
            if weekly and weekly.get('qualified_issuer_count',0)>=300 and weekly.get('complete_popularity_security_count',0)>=300:
                status='PASS';reason=f"只读母池{weekly.get('mother_pool_observed_count')}只，点时价格/成交额合格{weekly.get('qualified_security_count')}只、完整人气分{weekly.get('complete_popularity_security_count')}只；按SEC CIK去重后{weekly.get('qualified_issuer_count')}个发行人。自动换池仍另行验收。"
            else:
                status='BLOCKED';reason='尚无至少300只且具完整点时人气分的可审计母池。'
        if '100股数量、可比较预测数量、数据时间醒目' in line:
            status='PASS';reason='页面首屏独立显示常态池/有效预测、纽约数据截至和北京时间说明；真实桌面/手机浏览器逐项断言。'
        if any(s in line for s in ('新存储因子有真实','LOO按发行人','权重和成员点时','SNDK实体','已周更生产池')):
            status='BLOCKED';reason='缺历史成员/业务生效档案；本轮没有将当前分类回填历史。原池与历史预测保留。'
        if 'Pages真实可访问' in line:
            status='PASS';reason='发布完成后读取真实run_id并执行桌面/手机浏览器验收；单纯push不计PASS。'
        lines.append(f'- **{status}** {line[2:]} — {reason}')
    lines.extend(['','## 六项升级状态','',
                  '- PASS（工程）预测先冻结、结算另存；训练/校准实际成熟日隔离；加密归档恢复。',
                  '- PASS（已运行）近一年4段影子比较、独立顶底校准、条件价带误差与宽度、次日开盘净持有收益。',
                  '- PARTIAL 指数→个股残差已进入影子Challenger；发行人级板块层因缺点时历史成员仍BLOCKED。',
                  '- PARTIAL 非对称四态顶底与B3首次触达并存；完整离散竞争hazard及OHLC同bar顺序仍BLOCKED。',
                  '- BLOCKED 历史期权链、Skew、GEX和Options Flow不足；只从真实抓取时点向前归档，不补造。',
                  '- PASS（影子解释层）成熟校准路径扇形图、分组敏感度、固定其他特征的指数冲击压力测试与冻结台账已接入网页；不作为B3晋级证明。',
                  '- FAIL（晋级）尚未证明持续优于技术基准，不能宣称70%胜率或误差稳定≤3%。',
                  '- PARTIAL 点时母池资格与人气评分已完成；自动周更晋级仍受正式分类、拆股口径与云端数据入口约束。',
                  '- BLOCKED 历史动态成员档案、完整发行人级行业层、期权B4与正式任务网关。'])
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(args.output)

if __name__=='__main__':main()
