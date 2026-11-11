"""MCP 连接：把外面的 MCP 服务器（本机命令或网址）接进来，它们的工具和自带工具一样给智能体用。

配置在 data/mcp.json：
    {"servers": {"名字": {"command": "npx", "args": [...], "env": {...}, "cwd": "...", "enabled": true, "timeout": 120},
                 "名字2": {"url": "https://…/mcp", "headers": {...}, "enabled": true}}}
也认 Claude 等软件的写法 {"mcpServers": {...}}，可以整段粘贴进来。

给模型的工具名是 mcp__<服务器>__<工具>。
"""
import base64
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import __version__, config

PROTOCOL = '2025-06-18'
FILE = lambda: config.HOME / 'mcp.json'


def load():
    path = FILE()
    if not path.is_file():
        return {'servers': {}}
    data = json.loads(path.read_text(encoding='utf-8') or '{}')
    return {'servers': data.get('servers') or data.get('mcpServers') or {}}


def slug(name):
    """工具名只能用字母数字、_ 和 -：中文服务器名换成拼音不好做，就用 s + 序号兜住。"""
    text = re.sub(r'[^0-9A-Za-z_-]+', '_', name).strip('_')
    return text[:24] or 's' + uuid.uuid5(uuid.NAMESPACE_URL, name).hex[:6]


class Server:
    def __init__(self, name, spec):
        self.name, self.spec, self.key = name, spec, slug(name)
        self.state, self.error, self.tools, self.info, self.log = 'off', '', [], {}, []
        self.process, self.waiting, self.ids, self.lock = None, {}, itertools.count(1), threading.Lock()
        self.session, self.client, self.started = None, None, None

    @property
    def kind(self):
        return 'url' if self.spec.get('url') else 'command'

    # ----- 连接 -----

    def start(self):
        self.stop()
        self.state, self.error, self.tools = 'connecting', '', []
        try:
            if self.kind == 'command':
                self._spawn()
            else:
                self.client = httpx.Client(timeout=float(self.spec.get('timeout') or 120), headers=self.spec.get('headers') or {})
            result = self.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                                 'clientInfo': {'name': 'lab-workbench', 'version': __version__}})
            self.info = {'server': result.get('serverInfo') or {}, 'protocol': result.get('protocolVersion'),
                         'instructions': (result.get('instructions') or '')[:2000]}
            self.notify('notifications/initialized')
            self.refresh()
            self.state, self.started = 'connected', time.time()
        except Exception as error:      # 连不上：记下原因给人看，不影响别的服务器
            self.state, self.error = 'error', f'{type(error).__name__}: {error}'
            self.stop(keep_state=True)

    def refresh(self):
        tools, cursor = [], None
        while True:
            page = self.request('tools/list', {'cursor': cursor} if cursor else {})
            tools += page.get('tools') or []
            cursor = page.get('nextCursor')
            if not cursor:
                break
        self.tools = tools

    def stop(self, keep_state=False):
        if self.process:
            try:
                self.process.kill()
            except OSError:
                pass
        if self.client:
            self.client.close()
        self.process, self.client, self.session = None, None, None
        for slot in self.waiting.values():
            slot['done'].set()
        self.waiting = {}
        if not keep_state:
            self.state = 'off'

    def _spawn(self):
        command = self.spec['command']
        found = shutil.which(command) or command          # npx → npx.cmd
        args = [found] + [str(item) for item in self.spec.get('args') or []]
        if found.lower().endswith(('.cmd', '.bat')):
            args = ['cmd', '/c'] + args
        env = {**os.environ, **{key: str(value) for key, value in (self.spec.get('env') or {}).items()}}
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=self.spec.get('cwd') or None, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True, name=f'mcp-{self.key}').start()
        threading.Thread(target=self._read_err, daemon=True, name=f'mcp-{self.key}-err').start()

    def _read(self):
        for raw in self.process.stdout:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('{'):
                continue
            message = json.loads(line)
            self._handle(message)
        if self.state == 'connected':
            self.state, self.error = 'error', '服务器进程退出了。'
        for slot in self.waiting.values():
            slot['done'].set()

    def _read_err(self):
        for raw in self.process.stderr:
            self.log = (self.log + [raw.decode('utf-8', 'replace').rstrip()])[-60:]

    def _handle(self, message):
        if 'id' in message and ('result' in message or 'error' in message):
            slot = self.waiting.get(message['id'])
            if slot:
                slot['reply'] = message
                slot['done'].set()
        elif 'id' in message and 'method' in message:          # 服务器反过来问我们：只答 ping，其它说不支持
            reply = {'jsonrpc': '2.0', 'id': message['id']}
            reply.update({'result': {}} if message['method'] == 'ping' else {'error': {'code': -32601, 'message': '不支持'}})
            self._send(reply)
        elif message.get('method') == 'notifications/tools/list_changed':
            threading.Thread(target=self.refresh, daemon=True).start()

    def _send(self, message):
        data = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
        with self.lock:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    # ----- 收发 -----

    def notify(self, method, params=None):
        message = {'jsonrpc': '2.0', 'method': method, **({'params': params} if params else {})}
        if self.kind == 'command':
            self._send(message)
        else:
            self._post(message)

    def request(self, method, params, timeout=None):
        message = {'jsonrpc': '2.0', 'id': next(self.ids), 'method': method, 'params': params}
        timeout = timeout or float(self.spec.get('timeout') or 120)
        if self.kind == 'command':
            slot = {'done': threading.Event(), 'reply': None}
            self.waiting[message['id']] = slot
            self._send(message)
            slot['done'].wait(timeout)
            self.waiting.pop(message['id'], None)
            reply = slot['reply']
            if reply is None:
                raise TimeoutError(f'{self.name} 在 {timeout:.0f} 秒内没回话（{method}）。' + (f'最后的输出：{self.log[-1]}' if self.log else ''))
        else:
            reply = self._post(message)
        if 'error' in reply:
            raise RuntimeError(f'{self.name} 报错：{reply["error"].get("message")}')
        return reply.get('result') or {}

    def _post(self, message):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        if message.get('method') != 'initialize':
            headers['MCP-Protocol-Version'] = PROTOCOL
        response = self.client.post(self.spec['url'], content=json.dumps(message, ensure_ascii=False).encode('utf-8'), headers=headers)
        response.raise_for_status()
        self.session = response.headers.get('mcp-session-id') or self.session
        if 'id' not in message:
            return None
        if response.headers.get('content-type', '').startswith('text/event-stream'):
            for block in response.text.split('\n\n'):
                data = '\n'.join(line[5:].strip() for line in block.splitlines() if line.startswith('data:'))
                if data:
                    reply = json.loads(data)
                    if reply.get('id') == message['id']:
                        return reply
            raise RuntimeError(f'{self.name} 的回话里没有答案。')
        return response.json()

    def call(self, tool, arguments):
        return self.request('tools/call', {'name': tool, 'arguments': arguments or {}})


class Hub:
    def __init__(self):
        self.servers = {}

    def start(self):
        """按配置连上所有打开的服务器（各自在后台连，慢的不挡别的）。"""
        for server in self.servers.values():
            server.stop()
        self.servers = {name: Server(name, spec) for name, spec in load()['servers'].items()}
        for server in self.servers.values():
            if server.spec.get('enabled', True):
                threading.Thread(target=server.start, daemon=True, name=f'mcp-start-{server.key}').start()

    def restart(self, name):
        server = self.servers[name]
        server.spec = load()['servers'].get(name, server.spec)
        server.start()
        return self.view(name)

    def save(self, servers):
        FILE().write_text(json.dumps({'servers': servers}, ensure_ascii=False, indent=1), encoding='utf-8')
        self.start()

    def view(self, name=None):
        items = []
        for server in self.servers.values():
            if name and server.name != name:
                continue
            spec = server.spec
            items.append({'name': server.name, 'key': server.key, 'kind': server.kind, 'state': server.state if spec.get('enabled', True) else 'off',
                          'enabled': spec.get('enabled', True), 'error': server.error, 'info': server.info, 'log': server.log[-12:],
                          'where': spec.get('url') or ' '.join([spec.get('command', '')] + [str(item) for item in spec.get('args') or []]),
                          'tools': [{'name': tool['name'], 'description': tool.get('description') or '', 'params': list((tool.get('inputSchema') or {}).get('properties') or {})}
                                    for tool in server.tools]})
        return items[0] if name else items

    def definitions(self):
        out = []
        for server in self.servers.values():
            if server.state != 'connected':
                continue
            for tool in server.tools:
                schema = tool.get('inputSchema') or {'type': 'object', 'properties': {}}
                out.append({'name': f'mcp__{server.key}__{tool["name"]}'[:64],
                            'description': f'[MCP · {server.name}] ' + (tool.get('description') or tool['name'])[:900],
                            'input_schema': {'type': 'object', 'properties': schema.get('properties') or {}, 'required': schema.get('required') or []}})
        return out

    def find(self, wire_name):
        for server in self.servers.values():
            for tool in server.tools:
                if f'mcp__{server.key}__{tool["name"]}'[:64] == wire_name:
                    return server, tool['name']
        return None, None

    def call(self, ctx, wire_name, arguments):
        """调一个 MCP 工具，返回 (文字, 是否出错)。图片存进工作区并放进 look，下一条消息模型就能看到。"""
        server, tool = self.find(wire_name)
        if not server:
            return f'没有这个 MCP 工具：{wire_name}（服务器可能断了，请他在"助手 → 工具和连接"里看）。', True
        result = server.call(tool, arguments)
        texts = []
        for part in result.get('content') or []:
            kind = part.get('type')
            if kind == 'text':
                texts.append(part.get('text') or '')
            elif kind in ('image', 'audio'):
                suffix = (part.get('mimeType') or 'image/png').split('/')[-1].split('+')[0]
                path = ctx.folder / 'mcp' / f'{server.key}_{uuid.uuid4().hex[:6]}.{suffix}'
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(base64.b64decode(part.get('data') or ''))
                ctx.files.append(str(path.relative_to(ctx.folder)))
                if kind == 'image':
                    ctx.look.append(str(path))
                texts.append(f'[{kind}：{path.name}]')
            elif kind == 'resource':
                resource = part.get('resource') or {}
                texts.append(resource.get('text') or f'[资源 {resource.get("uri", "")}]')
            elif kind == 'resource_link':
                texts.append(f'[链接 {part.get("name") or ""} {part.get("uri", "")}]')
        if not texts and result.get('structuredContent') is not None:
            texts.append(json.dumps(result['structuredContent'], ensure_ascii=False))
        return '\n'.join(texts) or '（空）', bool(result.get('isError'))


hub = Hub()
