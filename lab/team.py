"""智能体团队：几个专职成员，各有角色、常用技能、模型。主助手协调，长的、专门的活派给成员在后台做，做完结果送回派活的对话。

名单在 data/agents.json，人和助手都能改、能加人。成员的事项编号用成员 id 开头（例 synthesis-1110…），一看就知道是谁在做。
"""
import json

from . import config

# 外部执行器：不用工作台的模型，起一个 Codex 进程（走本机网关），经工作台的 MCP 服务用同一套工具（lab/external.py）
CODEX = {'id': 'codex', 'name': 'Codex（外部执行器）', 'skills': [], 'executor': {'command': 'codex', 'effort': 'high'},
         'role': '重活：长的计算和数据处理、写程序批量算、整理大量文件、做大报告。它在自己的进程里跑（本机网关），用工作台的工具读写数据，要定的事发卡片。适合放后台做。'}

DEFAULT_TEAM = [
    {'id': 'main', 'name': '主助手', 'skills': [],
     'role': '协调。听他说，几步就能做完的自己做；长的、专门的活派给对应的成员在后台做，你接着处理别的；成员交回结果后汇总给他。'},
    {'id': 'synthesis', 'name': '合成设计', 'skills': ['树脂合成'],
     'role': '树脂合成：读研发路线和历史批次，设计下一个实验（投料、条件、取样计划），出 SOP 和批记录；算产率、闭合、偏差，解读过程曲线和 HPLC。'},
    {'id': 'analysis', 'name': '表征分析', 'skills': ['gpc', '椭偏', 'NK', '老化'],
     'role': 'GPC、椭偏膜厚、n/k、老化等测试：检查基线和积分区间、对比、找异常、解释趋势，把结论写回记录和课题节点。'},
    {'id': 'formulation', 'name': '配方与工单', 'skills': ['配方'],
     'role': '配方设计和核算、开工单、称量计划、备料和预混、DOE；按目标膜厚和 n/k 建议组成。'},
    {'id': 'intake', 'name': '数据导入', 'skills': [],
     'role': '仪器文件和各种记录的导入：认文件、按命名规则挂到样品、算好结果；处理数据体检里的断链、待关联、待确认。'},
    {'id': 'research', 'name': '文献调研', 'skills': [],
     'role': '查论文、专利、技术资料，读全文，交回带出处的简报；把结论记成命题和笔记，挂到课题节点。'},
    {'id': 'report', 'name': '报告与平台改进', 'skills': ['office', 'office-pptx', 'office-docx'],
     'role': '出实验报告、周报、组会 PPT；处理"改进"看板里的事：改呈现、写页面和技能。'},
    CODEX,
]


def path():
    return config.HOME / 'agents.json'


def members():
    return json.loads(path().read_text(encoding='utf-8')) if path().is_file() else [dict(item) for item in DEFAULT_TEAM]


def save(items):
    path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    return items


def get(agent_id):
    return next((item for item in members() if item['id'] == agent_id), None)


def of_lane(lane_id):
    """事项编号 → 哪个成员在做（主对话、规则、旧的分身都算主助手）。"""
    prefix = str(lane_id).split('-', 1)[0]
    return prefix if get(prefix) and prefix != 'main' else 'main'


def role_prompt(agent_id):
    me = get(agent_id) or get('main') or DEFAULT_TEAM[0]
    others = [item for item in members() if item['id'] != me['id']]
    lines = [f'# 你在团队里的角色：{me["name"]}', me.get('role') or '']
    if me.get('prompt'):                         # 他给这个成员另外交代的做法和习惯
        lines.append('他另外交代你的：' + me['prompt'])
    if me.get('skills'):
        lines.append('常用技能：' + '、'.join(me['skills']) + '（第一次碰到先用 skill 读）。')
    lines.append('团队里的其他成员（长的、专门的活用 delegate 派给他们，agent 填 id；background=true 放到后台，你接着做别的，做完结果会送回这里）：')
    lines += [f'- {item["id"]}：{item["name"]}——{item.get("role", "")[:60]}' for item in others]
    if me['id'] != 'main':
        lines.append('你是被派来做一件事的：做完用几句话交回结论和依据（数字带来源），做出的东西挂到记录上；要他定的事照样发卡片。')
    return '\n'.join(lines)
