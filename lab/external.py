"""外部执行器：别的智能体（先接 Codex，走本机网关）用工作台的工具干重活。

两半：
1. 工作台自己的 MCP 服务（Streamable HTTP，POST /mcp，只回 JSON）。一个连接属于一个事项（/mcp?lane=…）：
   每次工具调用都记进那个事项（人在助手页上看得到调了什么、结果是什么），ask_user 变成那个事项的卡片。
   不带 lane 的（例如他自己的 Claude Code 接上来）记进事项"外部连接（MCP）"。
2. 团队成员里带 executor 的（data/agents.json）：派给它的活不走工作台的模型，而是起一个外部进程
   （codex exec），把这个事项的 MCP 地址给它；它做完的最后一段话作为这个事项的回答，后台派的照样送回派活的对话。
   他回答了它发的卡片，事项再跑一轮：把回答连同之前的往来交给一个新的外部进程接着做。
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

from . import __version__, config, kernel, store, tools

PROTOCOL = '2025-06-18'
LOG_LANE = 'mcp-外部连接'
ASK_USER = {'name': 'ask_user',
            'description': '要他拍板的事发一张卡片到工作台（收件箱和这个事项里都看得到）。发完不用等：把不依赖这件事的部分做完，交回时说清在等他定什么；他回答后会叫你接着做。',
            'inputSchema': {'type': 'object', 'properties': {
                'title': {'type': 'string', 'description': '一句话的问题'}, 'text': {'type': 'string', 'description': '三行以内：现在的情况、为什么要他定'},
                'options': {'type': 'array', 'items': {'type': 'string'}, 'description': '2–4 个选项，推荐的放第一个'},
                'about': {'type': 'string', 'description': '留给接着干的自己：每个选项选了之后怎么做'}}, 'required': ['title']}}


# ---------- 1. MCP 服务 ----------

def _lane(lane_id):
    from . import loop
    lane = loop.lanes.get(lane_id or LOG_LANE)
    if not lane.data['title'] or lane.data['title'] == '新对话':
        lane.record('lane', title='外部连接（MCP）' if not lane_id else '外部执行器', library=config.settings()['library'])
    return lane


def _tool_list():
    found = [{'name': item['name'], 'description': item['description'], 'inputSchema': item['input_schema']} for item in tools.definitions()]
    return found + [ASK_USER]


def call(lane_id, name, args):
    """外面来的一次工具调用：照工作台自己的工具跑，记进事项。返回 (文字, 出错没有)。"""
    lane = _lane(lane_id)
    who = lane.data.get('executor') or ('外部连接' if not lane_id else '外部执行器')
    call_id = 'x-' + uuid.uuid4().hex[:8]
    lane.record('assistant', parts=[{'type': 'tool', 'id': call_id, 'name': name, 'input': args}], by=who, model=who, usage={}, stop='tool')
    lane.emit('tool_call', id=call_id, name=name, input=args)
    started = time.time()
    if name == 'ask_user':
        card = {'id': call_id, 'title': str(args.get('title') or ''), 'text': str(args.get('text') or ''),
                'options': [str(item) for item in args.get('options') or []], 'fields': [], 'about': str(args.get('about') or ''),
                'lane': lane.id, 'at': kernel.now(), 'key': '', 'evidence': [], 'refs': [], 'rule': False, 'results': []}
        lane.record('card', **card)
        lane.emit('card', card=card)
        from . import push
        push.toast('卡片', '要你拍板：' + card['title'], card['text'], f'#/assistant/{lane.id}', owner=lane.data.get('owner', ''))
        text, failed = '卡片发出去了。不用等：把不依赖这件事的部分做完再交回，交回时说清在等他定什么；他回答后会叫你接着做。', False
    else:
        ctx = tools.Context(lane.folder, lane.library(), lane)
        try:
            text, failed = tools.run(ctx, name, args or {})
        except Exception as error:                     # 工具里出的错照实告诉对方，让它改了再试
            text, failed = f'{type(error).__name__}: {error}', True
        for output in ctx.outputs:
            lane.record('output', **output)
    item = {'id': call_id, 'name': name, 'text': text, 'error': failed, 'seconds': round(time.time() - started, 1)}
    if name != 'ask_user':
        item.update({'files': list(ctx.files), **({'blocks': list(ctx.blocks)} if ctx.blocks else {}), **({'images': list(ctx.look)} if ctx.look else {})})
    lane.record('tools', results=[item])
    lane.emit('tool_result', **item)
    return text, failed


def handle(message, lane_id=''):
    """一条 JSON-RPC 消息 → 回复（通知返回 None）。"""
    method, params, mid = message.get('method'), message.get('params') or {}, message.get('id')
    if mid is None:
        return None
    reply = {'jsonrpc': '2.0', 'id': mid}
    if method == 'initialize':
        reply['result'] = {'protocolVersion': params.get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {'listChanged': False}},
                           'serverInfo': {'name': '研发工作台', 'version': __version__},
                           'instructions': '研发工作台的工具：产品库和知识库的记录、查找、计算、文献、学习、导出。数据从这些工具拿、写回也用它们；'
                                           '不要直接打开数据库文件。要他定的事用 ask_user 发卡片。回答用简体中文，数字带单位。'}
    elif method == 'ping':
        reply['result'] = {}
    elif method == 'tools/list':
        reply['result'] = {'tools': _tool_list()}
    elif method == 'tools/call':
        text, failed = call(lane_id, params.get('name'), params.get('arguments') or {})
        reply['result'] = {'content': [{'type': 'text', 'text': text}], 'isError': bool(failed)}
    else:
        reply['error'] = {'code': -32601, 'message': f'不认识的方法 {method}'}
    return reply


# ---------- 2. 外部执行器 ----------

def _history(lane):
    """这个事项到现在的往来：派来的活、它以前交回的话、他对卡片的回答。给新起的外部进程接着做。"""
    lines = []
    for message in lane.data['messages']:
        if message['role'] == 'user':
            lines.append('【派给你的活 / 新消息】\n' + (message.get('text') or ''))
        elif message['role'] == 'assistant':
            text = ''.join(part.get('text', '') for part in message.get('parts') or [] if part.get('type') == 'text').strip()
            if text:
                lines.append('【你上次交回的】\n' + text)
        elif message['role'] == 'tool':
            for result in message.get('results') or []:
                if result.get('name') == 'ask_user' and str(result.get('text', '')).startswith('他的回答'):
                    lines.append('【他对你发的卡片的回答】\n' + result['text'])
    return '\n\n'.join(lines)[-12000:]


def _prompt(lane, member):
    skills = '\n'.join(f'- {key}：{item["description"][:90]}' for key, item in tools._skills().items())
    return f'''你是研发工作台的外部执行器（{member["name"]}），替一位材料工程师（光刻旋涂膜材料研发）干重活。

怎么用工作台：
- 工作台的工具在 MCP 服务器 "workbench" 里：records（产品库、知识库的记录）、find、sql、sci（科学算法）、python、files、literature、lesson、export、show 等。数据从这些工具拿，写回也用它们；不要直接打开 data 目录下的数据库文件。
- 技能是工作台总结好的做法，用 workbench 的 skill 工具读：
{skills}
- 要他拍板的事用 workbench 的 ask_user 发卡片；发完不用等，把能做的做完、交回时说清在等他定什么。
- 你的工作目录是这个事项的工作区，做出来的文件放这里（他在工作台里看得到）。
- 规矩：简体中文；结论先说，数字带单位和来源；GPC 分子量是聚苯乙烯当量读数；时间用北京时间；不加他没要的东西。

{_history(lane)}

做完用几句话交回：结论、依据（数字带来源）、做了哪些文件、改了哪些记录。'''


def _command(member, lane, prompt_file, last_file):
    spec = member.get('executor') or {}
    port = int(os.environ.get('LAB_PORT') or 8850)
    exe = shutil.which(spec.get('command') or 'codex')
    if not exe:
        raise FileNotFoundError('这台电脑上找不到 codex 命令。')
    return [exe, 'exec', '--skip-git-repo-check', '-c', f'model_reasoning_effort="{spec.get("effort") or "high"}"', '-C', str(lane.folder),
            '-c', f'mcp_servers.workbench.url="http://127.0.0.1:{port}/mcp?lane={lane.id}"', '--output-last-message', str(last_file), '-']


def run(lane, member):
    """起外部进程做这一轮。返回事项状态：done / waiting（发了卡片在等他）/ stopped / failed。"""
    lane.data['executor'] = member['name']             # MCP 那边记工具调用时写"谁调的"
    prompt = _prompt(lane, member)
    stamp = store.now()[11:19].replace(':', '')            # 北京时间
    prompt_file, last_file, log_file = (lane.folder / f'_外部执行器_{stamp}_{name}' for name in ('任务.txt', '交回.txt', '过程.log'))
    prompt_file.write_text(prompt, encoding='utf-8')
    lane.record('note', role='info', text=f'交给{member["name"]}：它走本机网关，用工作台的工具干活，每一步都会出现在这里。')
    lane.emit('note', message=lane.data['messages'][-1])
    started = time.time()
    with open(log_file, 'w', encoding='utf-8', errors='replace') as log:
        process = subprocess.Popen(_command(member, lane, prompt_file, last_file), stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                   cwd=lane.folder, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process.stdin.write(prompt)
        process.stdin.close()
        while process.poll() is None:
            if lane.stop.is_set():
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                lane.record('note', role='info', text='他按了停止，外部执行器已停下。')
                return 'stopped'
            time.sleep(1)
    minutes = (time.time() - started) / 60
    final = last_file.read_text(encoding='utf-8', errors='replace').strip() if last_file.is_file() else ''
    if process.returncode and not final:
        tail = log_file.read_text(encoding='utf-8', errors='replace')[-1500:]
        lane.record('note', role='error', text=f'外部执行器没跑成（退出码 {process.returncode}）：\n{tail}')
        lane.emit('note', message=lane.data['messages'][-1])
        return 'failed'
    lane.record('assistant', parts=[{'type': 'text', 'text': final or '（外部执行器没有交回文字）'}], by=member['name'], model=member['name'],
                usage={}, stop='end')
    lane.emit('assistant_end', message={key: value for key, value in lane.data['messages'][-1].items() if key != 'raw'}, usage=lane.data['usage'])
    lane.record('note', role='info', text=f'外部执行器用了 {minutes:.1f} 分钟；过程记录在工作区 {log_file.name}。')
    return 'waiting' if lane.data['card'] else 'done'
