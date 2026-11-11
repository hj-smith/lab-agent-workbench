"""研发工作台的服务：一个 FastAPI。命令走普通接口，所有页面共用一条事件流（/api/stream）。

启动：python -I -B -X utf8 -m lab.server  （默认 http://127.0.0.1:8850）
"""
import asyncio
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import __version__, config, demos, insight, intake, interact, kernel, loop, mcp, model, people, present, store, tabular, team, tools, workspace

app = FastAPI(title='研发工作台')
FRESH = {'Cache-Control': 'no-cache'}
TEXTS = {'notes': (config.notes_dir, '.md'), 'pages': (config.pages_dir, '.html')}
PUBLIC_PORT = int(os.environ.get('LAB_PUBLIC_PORT') or 0)


@app.exception_handler(store.StoreError)
async def store_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=400)


@app.exception_handler(people.Problem)
async def people_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=error.status)


# ---------- 门：本机管理端不用登录（就是管理员）；外网访问口每个请求都要登录，再按这个人的权限 ----------
# 设计见 设计-用户和权限.md。管理员（你）在外面登录后也什么都能做。

OPEN = ('/login', '/api/auth/login', '/api/auth/remembered', '/web/login.html', '/web/invite.html', '/web/vendor/qrcode.js', '/favicon.ico',
        '/wechat/callback')   # 微信服务器来的回调：用签名验，不用登录
OPEN_PREFIX = ('/invite/', '/api/auth/invite/')


def _route(scope):
    from starlette.routing import Match
    for route in app.router.routes:
        match, child = route.matches(scope)
        if match == Match.FULL:
            return getattr(route.endpoint, '__name__', route.name), child.get('path_params', {})
    return None, {}


def _lane_meta(lane_id):
    item = loop.lanes.items.get(lane_id)
    if item:
        return {'id': lane_id, 'owner': item.data.get('owner', ''), 'rule': item.data['rule'], 'library': item.data['library'] or config.settings()['library']}
    with kernel.connect() as db:
        row = db.execute('SELECT id, owner, rule, library FROM lanes WHERE id=?', (lane_id,)).fetchone()
    return {'id': lane_id, 'owner': row['owner'], 'rule': row['rule'], 'library': row['library'] or config.settings()['library']} if row else {'id': lane_id, 'owner': None, 'rule': '', 'library': ''}


def _who(request):
    return getattr(request.state, 'person', None) or people.LOCAL


def _address(request):
    return request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '')


@app.middleware('http')
async def door(request: Request, call_next):
    port = (request.scope.get('server') or ('', 0))[1]
    if not PUBLIC_PORT or port != PUBLIC_PORT:
        request.state.person, request.state.public = people.LOCAL, False
        return await call_next(request)
    request.state.public = True
    path = request.url.path
    if path in OPEN or path.startswith(OPEN_PREFIX):
        request.state.person = None
        return await call_next(request)
    who = people.from_session(request.cookies.get('lab_s'), _address(request))
    if not who:
        if path.startswith('/api/') or path.startswith('/mcp'):
            return JSONResponse({'detail': '请先登录。', 'login': True}, status_code=401)
        from urllib.parse import quote
        return Response(status_code=303, headers={'Location': '/login?next=' + quote(path)})
    request.state.person = who
    endpoint, params = _route(request.scope)
    problem = people.route_problem(who, endpoint, request.method, params) if endpoint else None
    if not problem and endpoint and 'lane_id' in params:
        problem = people.lane_problem(who, endpoint, _lane_meta(params['lane_id']))
    if problem:
        return JSONResponse({'detail': problem}, status_code=403)
    return await call_next(request)


def _visible(who):
    """这个人能看到的对话 id。"""
    return {item['id'] for item in kernel.lanes() if people.lane_visible(who, item)}


def _inbox(who):
    view = loop.inbox.view()
    if who['admin']:
        return view
    seen = _visible(who)
    return {key: [item for item in value if item.get('lane') in seen] if isinstance(value, list) and key != 'timers' else value
            for key, value in view.items()} | {'timers': []}


def _public_url():
    return (config.settings().get('public_url') or '').rstrip('/') or (f'http://127.0.0.1:{PUBLIC_PORT}' if PUBLIC_PORT else '')


def _cookie(response, request, name, value, seconds):
    secure = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'
    response.set_cookie(name, value, max_age=seconds, httponly=True, samesite='lax', secure=secure, path='/')


@app.get('/login')
def login_page():
    return FileResponse(config.WEB / 'login.html', headers=FRESH)


@app.get('/invite/{token}')
def invite_page(token: str):
    return FileResponse(config.WEB / 'invite.html', headers=FRESH)


@app.get('/api/auth/remembered')
def auth_remembered(request: Request):
    """这台设备记住了谁（登录页只问验证码）。"""
    return {'person': people.remembered(request.cookies.get('lab_r'))}


@app.post('/api/auth/login')
def auth_login(request: Request, data: dict = Body(...)):
    remembered = people.remembered(request.cookies.get('lab_r'))
    who = people.authenticate(data.get('username') or (remembered or {}).get('username'), data.get('password'), data.get('code'),
                              (remembered or {}).get('id', '') if not data.get('password') else '')
    people.record_login(who['id'], _address(request))
    response = JSONResponse({'ok': True, 'home': '/m' if (who['rights'] or {}).get('home') == '现场' and not who['admin'] else '/'})
    _cookie(response, request, 'lab_s', people.session_cookie(who['id']), people.SESSION_SECONDS)
    if data.get('remember'):
        _cookie(response, request, 'lab_r', people.remember_cookie(who['id']), people.REMEMBER_SECONDS)
    return response


@app.post('/api/auth/logout')
def auth_logout(request: Request, data: dict = Body(default={})):
    response = JSONResponse({'ok': True})
    response.delete_cookie('lab_s', path='/')
    if data.get('forget'):
        response.delete_cookie('lab_r', path='/')
    return response


@app.get('/api/auth/me')
def auth_me(request: Request):
    who = _who(request)
    return {'person': who, 'public': request.state.public, 'recovery_left': people.recovery_left(who['id']) if who['id'] else None,
            'libraries': people.libraries(who)}


@app.post('/api/auth/password')
def auth_password(request: Request, data: dict = Body(...)):
    people.change_password(_who(request)['id'], data.get('old'), data.get('new'), data.get('code'))
    return {'ok': True}


@app.post('/api/auth/logout-others')
def auth_logout_others(request: Request):
    """别的设备都退出（这台接着用）。"""
    who = _who(request)
    people.kick(who['id'])
    import time as _time
    _time.sleep(1.1)
    response = JSONResponse({'ok': True})
    _cookie(response, request, 'lab_s', people.session_cookie(who['id']), people.SESSION_SECONDS)
    return response


@app.get('/api/auth/invite/{token}')
def auth_invite_start(token: str):
    return people.invite_start(token)


@app.post('/api/auth/invite/{token}')
def auth_invite_finish(token: str, request: Request, data: dict = Body(...)):
    out = people.invite_finish(token, data.get('name'), data.get('username'), data.get('password'), data.get('code'))
    who = out['person']
    people.record_login(who['id'], _address(request))
    response = JSONResponse({'recovery': out['recovery'], 'home': '/m' if (who['rights'] or {}).get('home') == '现场' and not who['admin'] else '/'})
    _cookie(response, request, 'lab_s', people.session_cookie(who['id']), people.SESSION_SECONDS)
    return response


# ---------- 用户和权限（管理员：本机，或你的外网管理员账号） ----------

@app.get('/api/users')
def users_list():
    return {'people': people.people(), 'roles': people.ROLES, 'libraries': config.libraries(), 'knowledge': config.KNOWLEDGE,
            'public_url': config.settings().get('public_url') or '', 'public_port': PUBLIC_PORT, 'admin_ready': people.admins_exist()}


@app.post('/api/users')
def users_invite(data: dict = Body(...)):
    out = people.invite(data.get('name'), data.get('note'), data.get('role') or '研发工程师', data.get('rights'), data.get('expires') or 0)
    return {'person': out['person'], 'link': f'{_public_url()}/invite/{out["token"]}'}


@app.put('/api/users/{person_id}')
def users_update(person_id: str, data: dict = Body(...)):
    return {'person': people.update(person_id, data)}


@app.post('/api/users/{person_id}/reinvite')
def users_reinvite(person_id: str):
    out = people.reinvite(person_id)
    return {'person': out['person'], 'link': f'{_public_url()}/invite/{out["token"]}'}


@app.post('/api/users/{person_id}/kick')
def users_kick(person_id: str):
    people.kick(person_id)
    return {'ok': True}


@app.delete('/api/users/{person_id}')
def users_remove(person_id: str):
    people.remove(person_id)
    return {'ok': True}


def _settings():
    value = config.settings()
    for profile in value['profiles']:
        profile['has_key'] = bool(config.key_for(profile['id']))
        rate = model.price(profile, profile.get('model'))
        profile['known_price'] = list(rate) if rate else None
    from . import push
    phone = config.key_for(push.PHONE_KEY)
    value['phone_push'] = {'set': bool(phone), 'channel': push.channel(phone) if phone else ''}      # 地址本身含密钥，不给页面
    return value


# ---------- 页面 ----------

@app.get('/')
def index():
    return FileResponse(config.WEB / 'index.html', headers=FRESH)


@app.get('/api/mobile/{library}')
def mobile_lists(library: str, request: Request):
    """手机页的三张单子：等你拍板的卡片、没称完的工单、最近的树脂实验（只取列表要的几个字段，不拉整份设计）。"""
    lib = store.open(library)
    def pick(kind, fields):
        if not lib.kind(kind):
            return []
        names = ', '.join(f"json_extract(data, '$.{name}')" for name in fields)
        _, rows = lib.sql(f'SELECT id, name, updated_at, {names} FROM records WHERE kind=? AND deleted_at IS NULL ORDER BY updated_at DESC', (kind,))
        return [{'id': row[0], 'name': row[1], 'updated': row[2], **dict(zip(fields, row[3:]))} for row in rows]
    orders = [row for row in pick('工单', ['配方编号', '计划日期', '状态', '总量']) if row['状态'] != '已完成']
    orders.sort(key=lambda row: str(row['计划日期'] or ''), reverse=True)
    batches = pick('树脂实验', ['实验名称', '目的', '状态'])
    number = lambda name: [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', str(name))]
    batches.sort(key=number, reverse=True)                                     # 编号大的（新的）在前
    batches.sort(key=lambda row: row['状态'] == '历史资料')                     # 还在做的在最前
    return {'cards': _inbox(_who(request))['cards'], 'orders': orders[:40], 'batches': batches[:60]}


@app.post('/api/mobile/{library}/{record_id}/photo/{filename}')
async def mobile_photo(library: str, record_id: str, filename: str, request: Request, note: str = ''):
    """手机上拍照记一笔：照片存成这条记录的原始文件，记录的"现场照片"里加一条（照片、说明、时间、谁拍的）。"""
    folder = config.HOME / '导入' / 'photos'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = store.now().replace(':', '').replace(' ', '-')
    path = folder / f'{stamp}-{Path(filename).name}'
    path.write_bytes(await request.body())
    lib = store.open(library)
    item = lib.get(record_id)
    file_id = lib.add_file(path, item['kind'], note=note or '现场照片', subfolder='现场照片')
    who = _who(request)
    photos = list(item['data'].get('现场照片') or []) + [{'照片': file_id, '说明': note.strip(), '时间': store.now()[:16], '谁': who['name'] if request.state.public else '你'}]
    lib.save(item['kind'], [{'id': item['id'], '现场照片': photos, 'files': [file_id]}], by=who['name'] if request.state.public else '人')
    return {'file': file_id, 'photos': photos}


@app.get('/api/mobile/{library}/{record_id}/photos')
def mobile_photos(library: str, record_id: str):
    return {'photos': store.open(library).get(record_id)['data'].get('现场照片') or []}


@app.get('/m')
def mobile():
    """手机页：现场填批记录、称量，回答卡片。"""
    return FileResponse(config.WEB / 'm.html', headers=FRESH)


@app.get('/web/{path:path}')
def web_file(path: str):
    target = config.WEB / path
    if not target.is_file():
        raise HTTPException(404, '没有这个文件')
    return FileResponse(target, headers=FRESH)


# ---------- 全局 ----------

@app.get('/api/state')
def state(request: Request):
    who = _who(request)
    libraries = people.libraries(who)
    default = config.settings()['library']
    me = {**who, 'public': request.state.public}
    if not who['admin']:                     # 别人：只给他能用的库、他看得到的卡片，不给模型和规则设置
        first = default if default in libraries else (libraries or [''])[0]
        return {'version': __version__, 'settings': {'library': first, 'push': {}}, 'libraries': libraries, 'library': first, 'formats': {},
                'efforts': [], 'default_profiles': [], 'default_ask_first': [], 'inbox': _inbox(who), 'rules': [], 'seq': loop.bus.n, 'me': me}
    return {'version': __version__, 'settings': _settings(), 'libraries': libraries, 'library': default,
            'formats': {key: value[0] for key, value in model.FORMATS.items()}, 'efforts': model.EFFORTS,
            'default_profiles': config.DEFAULT_PROFILES, 'default_ask_first': config.DEFAULT_ASK_FIRST,
            'inbox': loop.inbox.view(), 'rules': loop.rules.all(), 'seq': loop.bus.n, 'me': me, 'public_url': config.settings().get('public_url') or '',
            'public_port': PUBLIC_PORT}


@app.put('/api/settings')
def save_settings(data: dict = Body(...)):
    old = config.settings()
    profiles = [{key: value for key, value in profile.items() if key not in ('has_key', 'known_price', 't')} for profile in data.get('profiles', old['profiles'])]
    for profile in profiles:
        profile['id'] = profile.get('id') or config.new_profile_id()
    config.save_settings({**old, **{key: value for key, value in data.items() if key in ('active', 'ask_first', 'library', 'parallel', 'push', 'public_url')}, 'profiles': profiles})
    return _settings()


@app.put('/api/key')
def save_key(data: dict = Body(...)):
    config.save_key(data['profile'], (data.get('key') or '').strip())
    return {'saved': True}


@app.get('/api/models')
def models(profile: str):
    return model.models(config.profile(profile))


@app.get('/api/stream')
async def stream(request: Request, after: int = 0):
    who = _who(request)
    async def flow():
        last, quiet = int(request.headers.get('last-event-id') or after), 0
        seen = None if who['admin'] else _visible(who)          # 别人只收自己看得到的对话的事件
        while not await request.is_disconnected():
            fresh = loop.bus.after(last)
            if fresh:
                last, quiet = fresh[-1]['seq'], 0
                if seen is not None:
                    if any(item.get('lane') and item['lane'] not in seen for item in fresh):
                        seen = _visible(who)
                    fresh = [item for item in fresh if not item.get('lane') or item['lane'] in seen]
                    if not fresh:
                        continue
                yield ''.join(f'id: {item["seq"]}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n' for item in fresh)
            else:
                quiet += 1
                if quiet % 200 == 0:
                    yield ': 在\n\n'
                await asyncio.sleep(0.05)
    return StreamingResponse(flow(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ---------- 事项：对话、卡片、复查 ----------

@app.get('/api/lanes')
def lanes(request: Request, rules: bool = True):
    who = _who(request)
    running = {lane_id for lane_id, lane in loop.lanes.items.items() if lane.running}
    return {'lanes': [{**item, 'running': item['id'] in running} for item in kernel.lanes(rules) if people.lane_visible(who, item)]}


@app.get('/api/lanes/{lane_id}')
def lane(lane_id: str):
    item = loop.lanes.get(lane_id)
    item.seen()
    return item.snapshot()


@app.post('/api/say')
def say(request: Request, data: dict = Body(...)):
    who = _who(request)
    library = data.get('library') or config.settings()['library']
    if not who['admin']:
        if not people.level(who, library):
            raise HTTPException(403, f'你还没有「{library}」的权限。')
        if data.get('lane') and _lane_meta(data['lane'])['owner'] not in (None, who['id']):
            raise HTTPException(403, '这段对话不是你的，只能看。')
    lane_id = loop.say(data.get('lane') or '', data.get('text') or '', data.get('files') or [], data.get('anchor') or '', library, owner=who['id'])
    return {'lane': lane_id}


@app.post('/api/lanes/{lane_id}/answer')
def answer(lane_id: str, data: dict = Body(...)):
    if not loop.answer(lane_id, data.get('text') or '', data.get('values')):
        raise HTTPException(409, '这张卡片已经回答过了。')
    return {'ok': True}


@app.post('/api/lanes/{lane_id}/rewind')
def rewind_lane(lane_id: str, data: dict = Body(...)):
    """从他说的某句话重来：这句和后面的收起来，接着由页面重发改过的话。"""
    return loop.rewind(lane_id, data['to'])


@app.post('/api/lanes/{lane_id}/fork')
def fork_lane(lane_id: str, request: Request, data: dict = Body(...)):
    """另开一段：新对话带着到分叉点为止的内容，原对话不动。"""
    return {'lane': loop.fork(lane_id, data['upto'], owner=_who(request)['id'] if request.state.public else None)}


@app.post('/api/lanes/{lane_id}/close')
def close_card(lane_id: str, data: dict = Body(default={})):
    """他点了"不用问了"：卡片收起来，记一条决定，不叫醒助手。"""
    return {'ok': loop.close(lane_id, data.get('reason') or '', by='人')}


@app.get('/api/decisions/{library}')
def decisions(library: str):
    from . import decide
    lib = store.open(library)
    rows = lib.rows(decide.KIND) if lib.kind(decide.KIND) else []
    return {'items': sorted(rows, key=lambda row: row.get('时间') or '', reverse=True)}


@app.post('/api/decisions/{library}/{record_id}')
def decision_change(library: str, record_id: str, data: dict = Body(...)):
    """重新问（状态改成撤销，助手下次可以再问）或设为规矩（记进约定口径）。"""
    from . import decide
    lib = store.open(library)
    row = lib.get(record_id)
    if data.get('action') == 'rule':
        decide.to_rule(library, row['data'].get('问题') or row['name'], row['data'].get('回答') or '')
        lib.save(decide.KIND, [{'id': record_id, '规矩': True}], by='人')
    else:
        lib.save(decide.KIND, [{'id': record_id, '状态': '撤销'}], by='人')
    return {'ok': True}


@app.get('/api/review/{library}')
def review_changes(library: str):
    """改动回看：技能、规则、定过的事、提示词落地以后用了几次、替他少问几次。"""
    from . import review
    return review.changes(library)


@app.post('/api/review/{library}/undo')
def review_undo(library: str, data: dict = Body(...)):
    """撤掉一条改动：技能进回收站（改过的内置就恢复内置），规则停用或启用，定过的事重新问，提示词恢复内置。"""
    action, key = data['action'], data['key']
    if action == 'skill':
        insight.drop_skill(key)
    elif action == 'rule':
        items = loop.rules.all()
        for item in items:
            if item['name'] == key:
                item['enabled'] = not item.get('enabled', True)
        loop.rules.save(items)
    elif action == 'decision':
        decision_change(library, key, {'action': 'reopen'})
    elif action == 'prompt':                            # 他改的那段放进回收站，回到内置的
        bin_ = config.HOME / '回收站' / '提示词' / f'系统提示词-{kernel.now()[:16].replace(":", "").replace(" ", "-")}.md'
        bin_.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(loop.prompt_path()), str(bin_))
    return {'ok': True}


@app.post('/api/lanes/{lane_id}/stop')
def stop(lane_id: str):
    loop.stop(lane_id)
    return {'ok': True}


@app.delete('/api/lanes/{lane_id}')
def delete_lane(lane_id: str):
    loop.stop(lane_id)
    kernel.append(lane_id, 'deleted', {})
    loop.lanes.forget(lane_id)
    with kernel.connect() as db:                       # 排着队、等着重试的那一轮也不跑了
        db.execute("UPDATE runs SET status='cancelled' WHERE lane=? AND status='queued'", (lane_id,))
    return {'ok': True}


@app.delete('/api/lanes/{lane_id}/timers/{timer_id}')
def cancel_timer(lane_id: str, timer_id: str):
    loop.lanes.get(lane_id).record('timer_done', id=timer_id, cancelled=True)
    loop.engine.poke()
    return {'ok': True}


@app.put('/api/lanes/{lane_id}/files/{name}')
async def upload(lane_id: str, name: str, request: Request):
    target = config.workspace(lane_id) / Path(name).name
    target.write_bytes(await request.body())
    return {'name': target.name, 'bytes': target.stat().st_size}


@app.get('/api/ws/{lane_id}/{path:path}')
def workspace_file(lane_id: str, path: str):
    target = config.workspace(lane_id) / path
    if not target.is_file():
        raise HTTPException(404, '没有这个文件')
    return _send_file(target)


def _send_file(target):
    """以前做的交互图写的是 ../plotly.min.js（相对文件夹），经网页打开时找不到：换成工作台自己那一份。"""
    if target.suffix.lower() in ('.html', '.htm'):
        text = target.read_text(encoding='utf-8', errors='replace').replace('src="../plotly.min.js"', 'src="/web/vendor/plotly.min.js"')
        return Response(text, media_type='text/html; charset=utf-8', headers=FRESH)
    return FileResponse(target, headers=FRESH)


@app.get('/api/runs')
def runs(limit: int = 100):
    return {'runs': kernel.runs(limit)}


@app.get('/api/rules')
def get_rules():
    return {'rules': loop.rules.all()}


@app.put('/api/rules')
def put_rules(data: dict = Body(...)):
    loop.rules.save(data['rules'])
    return {'rules': loop.rules.all()}


# ---------- 研发库 ----------

@app.get('/api/lib/{library}/kinds')
def kinds(library: str):
    return {'kinds': store.open(library).kinds()}


@app.put('/api/lib/{library}/kinds/{name}')
def define(library: str, name: str, data: dict = Body(...)):
    return store.open(library).define(name, data.get('about'), data.get('fields'), data.get('group'), bool(data.get('replace')))


@app.get('/api/lib/{library}/records')
def records(library: str, kind: str = '', q: str = '', deleted: bool = False, limit: int = 2000):
    lib = store.open(library)
    if q:
        found, total = lib.search(q, limit, kind or None)
        return {'rows': found, 'total': total}
    rows = lib.rows(kind, deleted)
    return {'rows': rows[:limit], 'total': len(rows)}


def _cross_name(ref):
    """别的库里的记录（写成"库:id"）：取它的名字。"""
    other, _, rid = ref.partition(':')
    if other not in config.libraries():
        return ''
    found = store.open(other).sql('SELECT name FROM records WHERE id = ?', (rid,))[1]
    return found[0][0] if found else ''


@app.get('/api/lib/{library}/records/{record_id}')
def record(library: str, record_id: str, series: bool = True):
    lib = store.open(library)
    item = lib.get(record_id, with_series=series)
    for link in (item or {}).get('links') or []:          # 知识库连到产品库（或反过来）的关联，显示名字
        if ':' in str(link.get('to')) and not link.get('to_name'):
            link['to_name'] = _cross_name(link['to'])
    return {**item, 'group': (lib.kind(item['kind']) or {}).get('group') or ''} if item else item


@app.post('/api/lib/{library}/records')
def save(library: str, data: dict = Body(...)):
    lib = store.open(library)
    olds = {row['id']: lib.get(row['id']) for row in data['rows'] if row.get('id')}
    saved = lib.save(data['kind'], data['rows'], data.get('source') or '人（工作台）', bool(data.get('replace')), by='人')
    if data['kind'] == '树脂物料':                  # 物料改了（分子量、密度、纯度…）：计算核心下次按新物料重启，投料表跟着变
        from .sci import resin
        resin.reset(library)
    for row in data['rows']:                       # 跟你学：人在详细信息里改了什么、确认了什么
        old = olds.get(row.get('id'))
        if not old:
            continue
        changed = {key: value for key, value in row.items() if key not in ('id', 'unconfirmed') and old['data'].get(key) != value}
        if changed:
            demos.note('改记录', library, old['id'], old['name'], {key: old['data'].get(key) for key in changed}, changed, {'类型': old['kind']})
        confirmed = [key for key in old.get('unconfirmed') or [] if 'unconfirmed' in row and key not in row['unconfirmed']]
        if confirmed:
            demos.note('确认待确认', library, old['id'], old['name'], {'待确认': old['unconfirmed']}, {'待确认': row['unconfirmed']},
                       {'类型': old['kind'], '确认': confirmed, '值': {key: old['data'].get(key) for key in confirmed}})
    return {'saved': saved}


@app.delete('/api/lib/{library}/records/{record_id}')
def delete(library: str, record_id: str):
    return {'deleted': store.open(library).delete(record_id)}


@app.put('/api/lib/{library}/records/{record_id}/files/{filename}')
async def attach_file(library: str, record_id: str, filename: str, request: Request):
    """人把原始文件挂到一条记录上：复制进库的原始文件夹，记录上加这个文件。"""
    folder = config.HOME / '导入' / 'attach'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(await request.body())
    lib = store.open(library)
    item = lib.get(record_id)
    file_id = lib.add_file(path, item['kind'], note='人挂上的')
    lib.save(item['kind'], [{'id': record_id, 'files': [file_id]}], by='人')
    return {'file': file_id}


@app.post('/api/lib/{library}/records/{record_id}/restore')
def restore(library: str, record_id: str):
    return {'restored': store.open(library).delete(record_id, restore=True)}


@app.post('/api/lib/{library}/sql')
def sql(library: str, data: dict = Body(...)):
    columns, rows = store.open(library).sql(data['query'])
    return {'columns': columns, 'rows': rows}


@app.get('/api/lib/{library}/results')
def results(library: str, method: str, kind: str = ''):
    return {'results': store.open(library).results(method, kind or None)}


@app.get('/api/lib/{library}/settings')
def lib_settings(library: str):
    return {'settings': store.open(library).settings()}


@app.put('/api/lib/{library}/settings/{key}')
def put_setting(library: str, key: str, data: dict = Body(...)):
    store.open(library).set_setting(key, data['value'], data.get('about'))
    if key.startswith('树脂.'):                   # 树脂计算核心启动时读这些规矩：改了就让它下次重读
        from .sci import resin
        resin.reset(library)
        present._TAGS.clear()                    # 实验卡上的因子标签也按新规则重抽
    return {'ok': True}


@app.get('/api/lib/{library}/files/{file_id}')
def raw_file(library: str, file_id: str):
    return FileResponse(store.open(library).file_path(file_id))


@app.put('/api/lib/{library}/import/{filename}')
async def import_upload(library: str, filename: str, request: Request):
    return tabular.preview(library, filename, await request.body())


@app.post('/api/lib/{library}/import/{token}/inspect')
def import_inspect(library: str, token: str, data: dict = Body(...)):
    return tabular.inspect(library, token, data.get('sheet'), data.get('kind'))


@app.post('/api/lib/{library}/import/{token}')
def import_commit(library: str, token: str, data: dict = Body(...)):
    return tabular.commit(library, token, data['kind'], data['name_column'], data.get('mapping') or {}, data.get('sheet'), data.get('about') or '')


@app.get('/api/lib/{library}/export')
def export(library: str, kind: str, ids: str = '', series: bool = False):
    from urllib.parse import quote
    content = tabular.export(library, kind, [item for item in ids.split(',') if item], series)
    name = f'{library}_{kind}_{kernel.now()[:10]}.xlsx'
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name)}"})


@app.get('/api/lib/{library}/recent')
def recent(library: str, limit: int = 30):
    """最近改过的记录（总览的"新进来的数据"）。"""
    columns, rows = store.open(library).sql('SELECT id, kind, name, updated_at FROM records WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?', (limit,))
    return {'rows': [dict(zip(columns, row)) for row in rows]}


# ---------- 总览 ----------

TEST_KINDS = ('GPC样品', '膜', '老化谱', 'NK谱', '树脂实验', '工单')


_preview = present.preview


@app.get('/api/present/{library}/{record_id}')
def present_record(library: str, record_id: str):
    return present.present(library, record_id)


@app.put('/api/present-layout/{kind}')
def present_layout(kind: str, data: dict = Body(...)):
    present.save_layout(kind, {'order': data.get('order') or [], 'hidden': data.get('hidden') or []})
    return present.layout(kind)


@app.get('/api/roadmap/{library}')
def roadmap(library: str):
    return {'roadmap': present.roadmap(library)}


@app.put('/api/roadmap/{library}/{kind}/{item_id}')
def roadmap_update(library: str, kind: str, item_id: str, data: dict = Body(...)):
    return {'roadmap': present.update_roadmap(library, kind, item_id, data)}


@app.get('/api/projects/{library}')
def project_tree(library: str):
    return workspace.tree(library)


@app.get('/api/projects/{library}/{key:path}')
def project_page(library: str, key: str):
    return workspace.page(library, key)


@app.get('/api/trace/{library}/{ref}')
def trace(library: str, ref: str):
    return workspace.trace(library, ref)


@app.get('/api/paper/{library}/{ref}')
def paper(library: str, ref: str):
    return workspace.paper(library, ref)


@app.put('/api/paper/{library}/{ref}')
def paper_save(library: str, ref: str, data: dict = Body(...)):
    return workspace.save_actual(library, ref, data.get('values') or {})


@app.get('/api/paper/{library}/{ref}/export')
def paper_export(library: str, ref: str, fmt: str = 'xlsx', filled: bool = False):
    path = workspace.export_paper(library, ref, fmt, filled)
    return FileResponse(path, filename=path.name)


@app.put('/api/paper/{library}/{ref}/import/{filename}')
async def paper_import(library: str, ref: str, filename: str, request: Request):
    folder = config.HOME / '导入' / 'batch'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(await request.body())
    return present.clean(workspace.import_paper(library, ref, path))


@app.post('/api/orders/{library}')
def order_new(library: str, data: dict = Body(...)):
    return workspace.new_order(library, data['formula'], data['total'], data.get('date'), data.get('note') or '')


@app.put('/api/orders/{library}/{ref}')
def order_weigh(library: str, ref: str, data: dict = Body(...)):
    return workspace.save_weighing(library, ref, data.get('rows') or {}, data.get('operator'), data.get('status'))


@app.get('/api/orders/{library}/export')
def order_export(library: str, refs: str, fmt: str = 'xlsx'):
    path = workspace.export_orders(library, [item for item in refs.split(',') if item], fmt)
    return FileResponse(path, filename=path.name)


# ---------- 交互视图 ----------

@app.get('/api/gpc/{library}/{ref}')
def gpc_view(library: str, ref: str):
    return interact.gpc_view(library, ref)


@app.post('/api/gpc/{library}/{ref}/try')
def gpc_try(library: str, ref: str, data: dict = Body(...)):
    return interact.gpc_try(library, ref, data.get('anchors'), data.get('regions'), data.get('curve'), data.get('method'), data.get('snap'))


@app.put('/api/gpc/{library}/{ref}')
def gpc_save(library: str, ref: str, data: dict = Body(...)):
    lib = store.open(library)
    old = lib.get(ref, 'GPC样品')
    mw = lambda record: ((record['results'].get('GPC分子量') or {}).get('value') or {}).get('Mw')
    result = interact.gpc_save(library, ref, data['anchors'], data['regions'], data.get('curve'), data.get('method'), data.get('snap'))
    new = lib.get(ref, 'GPC样品')
    keys = ('基线锚点', '积分区间', '基线方法')                # 跟你学：他把基线和区间调成了什么样
    demo = demos.note('GPC 基线和区间', library, new['id'], new['name'], {key: old['data'].get(key) for key in keys}, {key: new['data'].get(key) for key in keys},
                      {'所属项目': new['data'].get('所属项目'), '标准曲线': new['data'].get('标准曲线'), 'Mw 之前': mw(old), 'Mw 之后': mw(new)})
    return {**result, 'demo': demo} if isinstance(result, dict) else result


@app.get('/api/gpc/{library}/{ref}/template/{template}')
def gpc_template(library: str, ref: str, template: str):
    return interact.gpc_template(library, ref, template)


@app.post('/api/gpc/{library}/{ref}/as-template')
def gpc_as_template(library: str, ref: str, data: dict = Body(...)):
    return interact.gpc_as_template(library, ref, data['name'], data['anchors'], data['regions'], data.get('method'))


@app.post('/api/gpc-batch/{library}/preview')
def gpc_batch_preview(library: str, data: dict = Body(...)):
    return interact.gpc_batch_preview(library, data['ids'], data.get('source'), data.get('curve'), data.get('snap'))


@app.post('/api/gpc-batch/{library}/compare/{ref}')
def gpc_batch_compare(library: str, ref: str, data: dict = Body(...)):
    return interact.gpc_batch_compare(library, ref, data['plan'])


@app.post('/api/gpc-batch/{library}/save')
def gpc_batch_save(library: str, data: dict = Body(...)):
    return interact.gpc_batch_save(library, data['rows'])


@app.get('/api/gpc-exp/{library}/{ref}')
def gpc_exp(library: str, ref: str):
    from . import gpcexp
    return gpcexp.view(library, ref)


@app.post('/api/gpc-exp/{library}')
def gpc_exp_create(library: str, data: dict = Body(...)):
    from . import gpcexp
    return gpcexp.create(library, data['name'], data['ids'])


@app.put('/api/gpc-exp/{library}/{ref}/members')
def gpc_exp_members(library: str, ref: str, data: dict = Body(...)):
    from . import gpcexp
    return gpcexp.save_members(library, ref, data['members'])


@app.put('/api/gpc-exp/{library}/{ref}/kinetics')
def gpc_exp_kinetics(library: str, ref: str, data: dict = Body(...)):
    from . import gpcexp
    return gpcexp.save_kinetics(library, ref, data['metric'], data['models'])


@app.get('/api/gpc-cal/{library}')
def gpc_cal_groups(library: str):
    from . import gpccal
    return {'curves': gpccal.groups(library)}


@app.get('/api/gpc-cal/{library}/{ref}')
def gpc_cal(library: str, ref: str):
    from . import gpccal
    return gpccal.view(library, ref)


@app.post('/api/gpc-cal/{library}/{ref}/trial')
def gpc_cal_trial(library: str, ref: str, data: dict = Body(...)):
    from . import gpccal
    return gpccal.trial(library, ref, data['enabled'], data['degree'])


@app.post('/api/gpc-cal/{library}/{ref}/new')
def gpc_cal_new(library: str, ref: str, data: dict = Body(...)):
    from . import gpccal
    return gpccal.save_new(library, ref, data['enabled'], data['degree'], data['name'])


@app.put('/api/gpc-cal/{library}/{ref}/status')
def gpc_cal_status(library: str, ref: str, data: dict = Body(...)):
    from . import gpccal
    return gpccal.save_status(library, ref, data)


@app.post('/api/gpc-cal/{library}/{ref}/compare')
def gpc_cal_compare(library: str, ref: str, data: dict = Body(...)):
    from . import gpccal
    return gpccal.compare(library, ref, data.get('others') or [])


@app.get('/api/gpc-report/{library}')
def gpc_report(library: str, kind: str, refs: str, fmt: str = 'pdf'):
    """GPC 样品 / 比较 / 实验报告：PDF、Word、Excel，放在 产出/<库>/GPC报告。"""
    from . import gpcreport
    path = gpcreport.report(library, kind, [ref for ref in refs.split(',') if ref], fmt)
    return FileResponse(path, filename=path.name)


@app.get('/api/gpc-figure/{library}')
def gpc_figure(library: str, name: str, refs: str, fmt: str = 'png', size: str = 'single'):
    """单独一张期刊图（600 dpi 位图或 SVG）。"""
    from urllib.parse import quote
    from . import gpcreport
    blob, filename = gpcreport.figure(library, name, [ref for ref in refs.split(',') if ref], fmt, size)
    kinds = {'png': 'image/png', 'svg': 'image/svg+xml', 'tiff': 'image/tiff'}
    return Response(blob, media_type=kinds.get(fmt, 'application/octet-stream'), headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get('/api/thickness/{library}')
def thickness_list(library: str):
    from . import thickness
    return {'curves': thickness.listing(library), 'films': thickness.film_points(library)}


@app.get('/api/thickness/{library}/{ref}')
def thickness_view(library: str, ref: str):
    from . import thickness
    return thickness.view(library, ref)


@app.post('/api/thickness/{library}/analyse')
def thickness_analyse(library: str, data: dict = Body(...)):
    from . import thickness
    return thickness.analyse(data.get('points') or [], data.get('target'), data.get('mass'), data.get('solids'))


@app.put('/api/thickness/{library}')
def thickness_save(library: str, data: dict = Body(...)):
    from . import thickness
    return thickness.save(library, data.get('id'), data)


@app.get('/api/nk-pairs/{library}')
def nk_pairs(library: str):
    return {'pairs': interact.nk_pairs(library)}


@app.get('/api/nk-pair/{library}')
def nk_pair(library: str, before: str, after: str):
    return interact.nk_pair_view(library, before, after)


@app.post('/api/surrogate/{library}')
def surrogate_propose(library: str, data: dict = Body(...)):
    """代理模型和下一轮提议：每个结果一个高斯过程，留一验证给可信度，在做过的范围里找最可能达到目标的条件。"""
    from . import surrogate
    return surrogate.propose(data.get('objectives') or [], float(data.get('extrapolate') or 0), float(data.get('kappa') or 1))


@app.get('/api/literature')
def literature_view():
    """知识 → 文献：监测主题、新到的线索、文献库。"""
    from . import literature
    return literature.view()


@app.put('/api/literature/topics')
def literature_topics(data: dict = Body(...)):
    from . import literature
    return {'topics': literature.reset_topics() if data.get('reset') else literature.save_topics(data.get('items') or [])}


@app.post('/api/literature/check')
def literature_check(data: dict = Body(default={})):
    """现在查一遍（每个开着的主题问一次 OpenAlex；发出去的只有检索式）。"""
    from . import literature
    return literature.check(data.get('names'))


@app.post('/api/literature/lead/{lead_id}')
def literature_lead(lead_id: str, data: dict = Body(...)):
    from . import literature
    return literature.decide(lead_id, data.get('action'), data.get('reason') or '')


@app.put('/api/literature/pdf/{filename}')
async def literature_pdf(filename: str, request: Request, projects: str = ''):
    """把 PDF 拖进文献库：存原文件和每页文字，有 DOI 就补上题录。"""
    from . import literature
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix='lab-pdf-'))
    target = folder / Path(filename).name
    target.write_bytes(await request.body())
    who = _who(request)
    return await asyncio.to_thread(literature.add_pdf, target, Path(filename).name, [key for key in projects.split(',') if key], who['name'] if request.state.public else '你')


@app.get('/api/literature/find')
def literature_find(q: str, limit: int = 10):
    from . import literature
    return literature.find(q, limit)


@app.post('/mcp')
async def mcp_server(request: Request, lane: str = ''):
    """工作台自己的 MCP 服务（Streamable HTTP，只回 JSON）：外部执行器和他自己的智能体接上来用工作台的工具。"""
    from . import external
    body = await request.json()
    if isinstance(body, list):
        replies = [reply for reply in [await asyncio.to_thread(external.handle, item, lane) for item in body] if reply]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await asyncio.to_thread(external.handle, body, lane)
    return JSONResponse(reply) if reply else Response(status_code=202)


@app.get('/mcp')
def mcp_stream():
    return Response(status_code=405)                     # 不开服务器推送的流；客户端照规矩退回只用 POST


@app.get('/api/learn')
def learn_view():
    """知识 → 学习：学习列表、今天要复习的题、把握校准、能从哪些课题开始学。"""
    from . import learn
    return learn.view()


@app.post('/api/learn')
def learn_create(data: dict = Body(...)):
    from . import learn
    return {'id': learn.create(data.get('目标'), data.get('为什么学') or '', data.get('课题') or [])}


@app.get('/api/learn/{ref}')
def learn_detail(ref: str):
    from . import learn
    return learn.detail(ref)


@app.post('/api/learn/{ref}/answer')
def learn_answer(ref: str, data: dict = Body(...)):
    from . import learn
    return learn.answer(ref, data.get('question'), data.get('answer'), data.get('confidence'), data.get('thinking') or '',
                        int(data.get('hint') or 0), data.get('stage'))


@app.get('/api/scan-advice/{library}')
def scan_advice(library: str):
    """下一轮扫描建议：每条单因素扫描一张卡（旧平台 scan_advice 原样算）。"""
    from . import scans
    return scans.advice(library)


@app.put('/api/gpc-bands/{library}')
def gpc_bands(library: str, data: dict = Body(...)):
    from . import gpcexp
    return gpcexp.save_bands(library, data['project'], data['bands'])


@app.put('/api/gpc-batch/{library}/snap')
def gpc_snap(library: str, data: dict = Body(...)):
    return interact.gpc_snap_setting(library, data.get('snap'))


@app.get('/api/design/{library}/{ref}')
def design_view(library: str, ref: str):
    return interact.resin_design(library, ref)


@app.put('/api/design/{library}/{ref}/steps')
def design_steps(library: str, ref: str, data: dict = Body(...)):
    return interact.resin_design_steps(library, ref, data['steps'])


@app.get('/api/design/{library}/{ref}/next-code')
def design_next_code(library: str, ref: str):
    return {'code': interact.resin_next_code(library, ref)}


@app.post('/api/design/{library}/{ref}/derive')
def design_derive(library: str, ref: str, data: dict = Body(...)):
    return interact.resin_derive(library, ref, data['code'], data.get('title'))


@app.put('/api/design/{library}/{ref}')
def design_set(library: str, ref: str, data: dict = Body(...)):
    return interact.resin_design_set(library, ref, data.get('charges'), data.get('vars'))


@app.get('/api/formula/{library}/{ref}')
def formula_view(library: str, ref: str):
    return interact.formula_view(library, ref)


@app.post('/api/formula/{library}/calc')
def formula_calc(library: str, data: dict = Body(...)):
    return interact.formula_calc(library, data['fields'])


@app.put('/api/formula/{library}/{ref}')
def formula_save(library: str, ref: str, data: dict = Body(...)):
    old = store.open(library).get(ref, kind='配方')
    result = interact.formula_save(library, ref, data['fields'])
    changed = {key: value for key, value in data['fields'].items() if old['data'].get(key) != value}
    demos.note('配方修改', library, old['id'], old['name'], {key: old['data'].get(key) for key in changed}, changed, {})
    return result


@app.post('/api/premix/{library}')
def premix(library: str, data: dict = Body(...)):
    return interact.premix_plan(library, data['orders'])


@app.get('/api/ell/{library}')
def ell_days(library: str):
    return {'days': interact.ell_days(library)}


@app.get('/api/ell/{library}/{day}')
def ell_day(library: str, day: str):
    return interact.ell_day(library, day)


@app.put('/api/ell/{library}/film/{film}')
def ell_exclude(library: str, film: str, data: dict = Body(...)):
    old = store.open(library).get(film)
    result = interact.ell_exclude(library, film, data.get('items') or [])
    strip = lambda items: [{key: item.get(key) for key in ('读数id', '范围', '列', '原因')} for item in items or []]
    demos.note('椭偏剔点', library, old['id'], old['name'], {'剔掉': strip(old['data'].get('剔除'))}, {'剔掉': strip(data.get('items'))},
               {'原因': '；'.join(sorted({item.get('原因') for item in data.get('items') or [] if item.get('原因')}))})
    return result


@app.put('/api/ell/{library}/reference')
def ell_reference(library: str, data: dict = Body(...)):
    before = (store.open(library).setting('椭偏.人选参比') or {}).get(data['group'])
    result = interact.ell_reference(library, data['group'], data.get('reference'))
    demos.note('椭偏参比', library, data['group'], data['group'], {'参比': before}, {'参比': data.get('reference')}, {})
    return result


@app.get('/api/nk/{library}')
def nk_groups(library: str):
    return {'groups': interact.nk_groups(library)}


@app.get('/api/nk/{library}/{name}')
def nk_group(library: str, name: str):
    return interact.nk_group_view(library, name)


@app.put('/api/nk/{library}/spectrum/{record_id}')
def nk_toggle(library: str, record_id: str, data: dict = Body(...)):
    old = store.open(library).get(record_id)
    result = interact.nk_toggle(library, record_id, data['field'], data['value'])
    demos.note('n/k 纳入', library, old['id'], old['name'], {'值': old['data'].get(data['field'])}, {'值': bool(data['value'])},
               {'字段': data['field'], '分组': old['data'].get('分组')})
    return result


@app.get('/api/aging/{library}')
def aging_samples(library: str):
    return {'samples': interact.aging_samples(library)}


@app.get('/api/aging/{library}/{sample}')
def aging_view(library: str, sample: str):
    return interact.aging_view(library, sample)


@app.put('/api/aging/{library}/upload/{filename}')
async def aging_upload(library: str, filename: str, request: Request):
    folder = config.HOME / '导入' / 'aging' / kernel.now()[:10]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(await request.body())
    return interact.aging_preview(library, [path])[0]


@app.post('/api/aging/{library}/commit')
def aging_commit(library: str, data: dict = Body(...)):
    return {'saved': interact.aging_commit(library, data['items'])}


@app.get('/api/desk/{library}/lots')
def desk_lots(library: str):
    from .sci import desk
    return present.clean(desk.lot_table(store.open(library)))


@app.get('/api/desk/{library}/factors')
def desk_factors(library: str):
    from .sci import desk
    lib = store.open(library)
    table = desk.lot_table(lib)
    labels = {**{item['id']: item['label'] for item in desk._factor_defs(lib)}, **{key: label for key, label, _ in desk.RESPONSES}}
    return present.clean({'factors': table['factors'], 'responses': table['responses'], 'labels': labels, 'recommendations': desk.recommendations(lib)})


@app.get('/api/desk/{library}/factors/{factor}/{response}')
def desk_compare(library: str, factor: str, response: str):
    from .sci import desk
    return present.clean(desk.factor_compare(store.open(library), factor, response))


@app.get('/api/desk/{library}/health')
def desk_health(library: str):
    from .sci import health
    return present.clean(health.check(store.open(library)))


@app.post('/api/desk/{library}/health/confirm')
def desk_confirm(library: str, data: dict = Body(...)):
    from .sci import health
    return present.clean(health.confirm(store.open(library), data['items'], bool(data.get('remember'))))


@app.post('/api/desk/{library}/health/dismiss')
def desk_dismiss(library: str, data: dict = Body(...)):
    from .sci import health
    return present.clean(health.dismiss(store.open(library), data['items']))


# ---------- 仪器文件导入队列 ----------

@app.get('/api/importq/{library}/ping')
def importq_ping(library: str):
    return {'ok': True}


@app.put('/api/importq/{library}/{filename}')
async def importq_upload(library: str, filename: str, request: Request):
    from . import importq
    folder = config.HOME / '导入' / 'queue' / kernel.now()[:10]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(await request.body())
    return present.clean(intake.add(library, [path]))


@app.get('/api/intake/{library}')
def intake_list(library: str):
    """导入页：等你确认的、最近导入的（来自拖进来的和自动导入的文件夹）。"""
    return present.clean(intake.listing(library))


@app.post('/api/intake/{library}/go')
def intake_go(library: str, data: dict = Body(...)):
    """他定好了：每项直接导入、交给助手或不要。"""
    return present.clean(intake.go(library, data['rows']))


@app.get('/api/demos/{library}')
def demos_view(library: str, kind: str = ''):
    """跟你学：每类判断攒了几次、最近几条；给了 kind 就列这一类的示范。"""
    from . import decide
    if kind:
        return {'items': demos.rows(library, kind)}
    return {'kinds': demos.summary(library), 'decided': len(decide.recent(library, 500))}


@app.post('/api/demos/{library}/{demo_id}/why')
def demos_why(library: str, demo_id: str, data: dict = Body(...)):
    """他顺手写的一句"为什么这样改"：学的时候最有用。"""
    return {'ok': demos.why(demo_id, data.get('text') or '')}


@app.post('/api/demos/{library}/learn')
def demos_learn(library: str, data: dict = Body(...)):
    """让助手把一类示范归纳成做法：先问他，同意了才写进技能。"""
    kind = data['kind']
    history = ('另外，库里已经分析过的 GPC 样品（基线锚点、积分区间，大多是他过去在旧软件里定的）也是示范，可以一起看：用 sql 或 records 查 GPC样品 的 基线锚点、积分区间，'
               '需要时用 sci 取原始曲线，看锚点、区间和峰、溶剂峰的相对位置。\n') if kind == 'GPC 基线和区间' else ''
    text = (f'[跟你学] 他在平台上做过的「{kind}」判断，用 demos 工具看（kind={kind}）：每条有改前、改后、当时的情况。\n{history}'
            '找出他一贯的做法：什么情况下怎么判断，写成照着做就能做对的规则（带数、带适用条件，也写清例外和拿不准的情况）。先用 plan 列步骤。\n'
            '然后发一张卡片问他：key 写「跟你学：' + kind + '：' + kernel.now()[:10] + '」（每一轮单独一个），title 问要不要把这条做法写进技能（哪个技能），evidence 写依据（看了几次示范、几次符合、例外是哪几次），'
            'options 给"写进技能（推荐）""先不写""我来改"，rule=false。他同意了：把做法写进对应技能的 SKILL.md（没有就新建技能），再用 demos 的 used 把这些示范标成已学（写技能名）。'
            '不同意或要改：按他说的改。拿不出可靠规律（示范太少、互相矛盾）就直接告诉他还差什么，不发卡片。')
    lane = loop.say(kernel.new_id('learn'), text, (), '助手 · 跟你学', library)
    return {'lane': lane}


@app.get('/api/project-knowledge/{library}/{project_id}')
def project_knowledge(library: str, project_id: str):
    """一个课题挂着的知识：知识库里"属于"这个课题的命题（状态、把握、证据和出处几条），和产品库里这个课题的预测（命中情况）。"""
    from . import predict
    project = store.open(library).get(project_id)
    know = store.knowledge()
    _, rows = know.sql("SELECT r.id, r.name, json_extract(r.data,'$.陈述'), json_extract(r.data,'$.状态'), json_extract(r.data,'$.把握'), json_extract(r.data,'$.适用范围') "
                       "FROM records r JOIN links l ON l.from_id = r.id WHERE l.relation = '属于' AND l.to_id IN (?, ?, ?, ?) AND r.kind = '命题' AND r.deleted_at IS NULL",
                       (project['id'], project['name'], f'{library}:{project["id"]}', f'{library}:{project["name"]}'))   # 知识库连到产品库的记录写成"库:id"
    claims = []
    for claim_id, name, statement, state, sure, scope in rows:
        _, counts = know.sql("SELECT relation, count(*) FROM links WHERE from_id = ? AND relation IN ('证据', '出处') GROUP BY relation", (claim_id,))
        claims.append({'id': claim_id, 'name': name, '陈述': statement or name, '状态': state or '待验证', '把握': sure, '适用范围': scope, **dict(counts)})
    return {'claims': claims, 'predictions': predict.for_project(library, {'id': project['id'], 'name': project['name']})}


@app.get('/api/claims/{library}')
def claims_view(library: str):
    """知识页的命题：全部命题（状态、把握、证据和出处几条、属于哪些课题），和这个产品库的预测命中情况。"""
    from . import predict
    know = store.knowledge()
    rows = know.rows('命题') if know.kind('命题') else []
    _, links = know.sql("SELECT from_id, relation, to_id FROM links WHERE relation IN ('证据', '出处', '属于')")
    count = {}
    for source, relation, target in links:
        item = count.setdefault(source, {'证据': 0, '出处': 0, '属于': []})
        if relation == '属于':
            item['属于'].append(target)
        else:
            item[relation] += 1
    lib = store.open(library)
    names = {row['id']: row['name'] for row in lib.rows('课题')} if lib.kind('课题') else {}
    claims = [{'id': row['id'], '陈述': row.get('陈述') or row['name'], '状态': row.get('状态') or '待验证', '把握': row.get('把握'), '适用范围': row.get('适用范围'),
               '证据': count.get(row['id'], {}).get('证据', 0), '出处': count.get(row['id'], {}).get('出处', 0),
               '课题': [names.get(target.split(':', 1)[-1], target.split(':', 1)[-1]) for target in count.get(row['id'], {}).get('属于', [])]} for row in rows]
    predictions = [row for row in lib.rows(predict.KIND)] if lib.kind(predict.KIND) else []
    tally = {}
    for row in predictions:
        key = row.get('命中') or (row.get('状态') or '等结果')
        tally[key] = tally.get(key, 0) + 1
    return {'claims': claims, 'tally': tally}


@app.get('/api/prompt')
def prompt_view():
    """助手的系统提示词：基础部分（能改）和最后拼成的全文（主助手）。"""
    return {'text': loop.base_prompt(), 'custom': loop.prompt_path().is_file(), 'default': loop.PROMPT, 'full': loop.system_prompt('main')}


@app.put('/api/prompt')
def prompt_save(data: dict = Body(...)):
    """存他改的提示词；text 为空就回到内置的。下一轮就用新的。"""
    if (data.get('text') or '').strip() and data['text'].strip() != loop.PROMPT.strip():
        loop.prompt_path().write_text(data['text'], encoding='utf-8')
    else:
        loop.prompt_path().unlink(missing_ok=True)
    return prompt_view()


@app.post('/api/presence')
def presence(request: Request):
    """工作台页面在前台：这时候不弹桌面通知。只算管理员（本机，或外网登录的管理员）；别人在用不算"你在跟前"。"""
    from . import push
    if _who(request)['admin']:
        push.presence(str(request.base_url) if not request.state.public else None)
    return {'ok': True}


# ---------- 微信推送（微信官方公众号接口；先用公众平台测试号）：每个人扫码绑自己的微信 ----------

@app.get('/api/wechat/me')
def wechat_me(request: Request):
    from . import wechat
    who = _who(request)
    out = {'ready': wechat.ready(), 'bound': bool(wechat.uid_of(who['id'])), 'admin': who['admin']}
    if who['admin']:                           # 管理员看准备工作：appID、模板 ID、回调地址和 Token（appsecret 只说有没有）
        conf = wechat.conf()
        out.update(appid=conf.get('appid', ''), template=conf.get('template', ''), token=conf['token'], has_secret=bool(config.key_for(wechat.SECRET)),
                   callback=(_public_url() + '/wechat/callback') if config.settings().get('public_url') else '', public_url=config.settings().get('public_url') or '',
                   template_text=wechat.TEMPLATE)
    return out


@app.put('/api/wechat/setup')
def wechat_setup(data: dict = Body(...)):
    """管理员填测试号的 appID、appsecret（存 auth.json）、模板 ID。"""
    from . import wechat
    conf = wechat.conf()
    wechat.save_conf({**conf, 'appid': (data.get('appid') or conf.get('appid') or '').strip(), 'template': (data.get('template') or conf.get('template') or '').strip()})
    if data.get('secret'):
        config.save_key(wechat.SECRET, data['secret'].strip())
    wechat._token['value'] = ''
    return {'ready': wechat.ready()}


@app.post('/api/wechat/check')
def wechat_check():
    from . import wechat
    return wechat.check()


@app.post('/api/wechat/bind')
def wechat_bind(request: Request):
    from . import wechat
    return wechat.bind_code(_who(request))


@app.get('/api/wechat/poll')
def wechat_poll(code: str):
    from . import wechat
    return {'bound': wechat.poll(code)}


@app.post('/api/wechat/unbind')
def wechat_unbind(request: Request):
    from . import wechat
    wechat.save_uid(_who(request)['id'] or 'admin', '')
    return {'ok': True}


@app.post('/api/wechat/test')
def wechat_test(request: Request):
    from . import wechat
    who = _who(request)
    ok, note = wechat.send([wechat.uid_of(who['id'])], '研发工作台：微信推送试一下', '以后要你拍板的事、助手的通知会推到这里。', '#/overview')
    return {'ok': ok, 'note': note}


@app.get('/wechat/callback')
def wechat_callback_check(signature: str = '', timestamp: str = '', nonce: str = '', echostr: str = ''):
    """微信验证"接口配置信息"：签名对就原样回 echostr。"""
    from . import wechat
    return Response(echostr if wechat.callback_verify(signature, timestamp, nonce) else '', media_type='text/plain')


@app.post('/wechat/callback')
async def wechat_callback(request: Request, signature: str = '', timestamp: str = '', nonce: str = ''):
    """微信推来的事件（关注、扫码）：扫了绑定码的记下 openid。"""
    from . import wechat
    if not wechat.callback_verify(signature, timestamp, nonce):
        return Response('', status_code=403)
    return Response(wechat.callback_event(await request.body()) or 'success', media_type='application/xml')


@app.post('/api/push/phone-test')
def push_phone_test():
    """他点了"推一条试试"：往他设的手机推送地址发一条。"""
    from . import push
    ok, note = push.phone('研发工作台：手机推送试一下', '以后要你拍板的卡片、助手的通知，你不在电脑前时会推到这里。', '#/overview')
    return {'ok': ok, 'note': note}


@app.post('/api/push/test')
def push_test(request: Request):
    from . import push
    push.presence(str(request.base_url))
    return {'ok': push.toast('通知', '研发工作台：桌面通知试一下', '以后要你拍板的卡片、助手的通知，你不在工作台前时会这样弹出来。点我打开总览。', '#/overview', force=True)}


@app.post('/api/intake/scan')
def intake_scan():
    """现在就把自动导入的文件夹看一遍（不用等 30 秒）。"""
    from . import sources
    threading.Thread(target=sources.scan_folders, args=(loop.rules.all(),), daemon=True).start()
    return {'ok': True}


# ---------- 智能体团队 ----------

@app.get('/api/team')
def team_view():
    running = {lane_id for lane_id, lane in loop.lanes.items.items() if lane.running}
    with kernel.connect() as db:
        queued = {row[0] for row in db.execute("SELECT lane FROM runs WHERE status='queued'")}
    items = [{**item, 'agent': team.of_lane(item['id']), 'running': item['id'] in running, 'queued': item['id'] in queued}
             for item in kernel.lanes(False)]
    return {'members': team.members(), 'lanes': items[:300], 'profiles': [{'id': p['id'], 'name': p['name'], 'model': p.get('model')} for p in config.settings()['profiles']],
            'parallel': int(config.settings().get('parallel') or 4)}


@app.put('/api/team')
def team_save(data: dict = Body(...)):
    return {'members': team.save(data['members'])}


@app.post('/api/team/{agent}/task')
def team_task(agent: str, request: Request, data: dict = Body(...)):
    """人直接给某个成员派活：开一条这个成员的事项，在后台跑。"""
    who, library = _who(request), data.get('library') or config.settings()['library']
    if not who['admin'] and not people.level(who, library):
        raise HTTPException(403, f'你还没有「{library}」的权限。')
    if (team.get(agent) or {}).get('executor') and not people.switch(who, 'machine'):
        raise HTTPException(403, '这个成员要在这台电脑上跑程序，你还没有这个权限。')
    lane_id = kernel.new_id(agent if agent != 'main' else 'lane')
    return {'lane': loop.say(lane_id, data['text'], (), data.get('anchor') or '', library, owner=who['id'])}


@app.get('/api/conventions/{library}')
def conventions(library: str):
    return {'items': interact.conventions(library)}


@app.put('/api/conventions/{library}')
def conventions_save(library: str, data: dict = Body(...)):
    return {'items': interact.save_conventions(library, data['items'])}


@app.get('/api/experiments/{library}')
def experiment_cards(library: str):
    rows = present.experiments(library)
    return {'rows': rows, 'projects': workspace.experiment_projects(library, rows)}


@app.get('/api/gpc-overlay/{library}')
def gpc_overlay(library: str, ids: str):
    return interact.gpc_overlay(library, [item for item in ids.split(',') if item])


@app.get('/api/chart-preference/{library}')
def chart_preferences(library: str):
    return {'preferences': store.open(library).setting('作图偏好') or {}}


@app.post('/api/gpc-align/{library}')
def gpc_align(library: str, data: dict = Body(...)):
    """叠加对齐：每条的保留时间平移（只改显示）＋平移后按各自标准曲线重算的 Mw（不存），看漂移让分子量差了多少。"""
    from .sci import gpc
    lib = store.open(library)
    ids, method = data['ids'], data.get('method') or 'marker'
    manual = data.get('manual') or {}
    suggestion = gpc.suggest_marker(lib, ids) if method == 'marker' and not data.get('window') and not all(item in manual for item in ids) else None
    out = gpc.align(ids, data['ref'], lib, method, data.get('window') or (suggestion or {}).get('区间'), manual=data.get('manual'))
    for item, value in out['平移'].items():
        try:
            value['Mw对齐后'] = gpc.analyze_shifted(item, value['平移'], lib)['Mw'] if value['平移'] else None
        except Exception:
            value['Mw对齐后'] = None
    return present.clean({**out, '建议': suggestion})


@app.put('/api/chart-preference/{library}/{chart}')
def chart_preference(library: str, chart: str, data: dict = Body(...)):
    return {'preference': interact.save_chart_preference(library, chart, data)}


@app.post('/api/rules/{name}/run')
def rule_run(name: str, library: str = ''):
    """现在就跑一次这条规则（例如还没到点的晨报）。给了库就只在这个库跑，不给就按规则（"全部"就每个库各一份）。"""
    rule = loop.rules.get(name)
    event = kernel.record_event('定时', 'manual', name, f'手动跑「{name}」', {'every': '现在'})
    lane_ids = [loop.rules.lane(rule, item) for item in ([library] if library else loop.rules.libraries(rule))]
    for lane in lane_ids:
        kernel.queue(lane, event)
    loop.engine.poke()
    return {'lanes': lane_ids}


@app.get('/api/brief/{library}/latest')
def brief_latest(library: str, kind: str = '晨报'):
    from . import brief
    row = brief.last_brief(store.open(library), kind)
    if not row:
        return {'brief': None}
    # 总览上放"今天建议"（有 task 的那几条），没有建议才放发现
    items = [item for block in row.get('块') or [] if block.get('type') == 'items' for item in block.get('items') or []]
    todo = [item for item in items if item.get('task')] or items
    return {'brief': {'id': row['id'], 'name': row['name'], 'date': row.get('日期'), 'headline': row.get('一句话'),
                      'points': [item.get('title') for item in todo][:3]}}


@app.get('/api/resolve/{library}')
def resolve(library: str, name: str):
    """[[名字]] → 那条记录：先在当前产品库按名字找（重名时取最近改的），再找知识库。"""
    for lib in (store.open(library), store.knowledge()):
        found = lib.sql('SELECT id, kind FROM records WHERE name=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1', (name,))[1]
        if found:
            return {'library': lib.name, 'id': found[0][0], 'kind': found[0][1]}
    return {'library': None, 'id': None, 'kind': None}


@app.get('/api/formula-projects/{library}')
def formula_project_list(library: str):
    return {'projects': workspace.formula_projects(library)}


# 项目改名、挪进项目、排先后：合成（树脂实验）、配方、GPC（样品）三处一个办法
@app.get('/api/project-order/{library}/{scope}')
def project_order(library: str, scope: str):
    return {'order': workspace.project_order(library, scope)}


@app.put('/api/project-order/{library}/{scope}')
def project_order_save(library: str, scope: str, data: dict = Body(...)):
    return {'order': workspace.save_project_order(library, scope, data['names'])}


@app.post('/api/project-move/{library}/{scope}')
def project_move(library: str, scope: str, data: dict = Body(...)):
    return workspace.project_move(library, scope, data['ids'], data.get('name'))


@app.post('/api/project-rename/{library}/{scope}')
def project_rename(library: str, scope: str, data: dict = Body(...)):
    return workspace.project_rename(library, scope, data['old'], data['new'], data.get('ids') or [], data.get('record'))


@app.get('/api/gallery/{library}')
def gallery(library: str, kind: str):
    return {'rows': present.gallery(library, kind)}

@app.get('/api/overview/{library}')
def overview(library: str):
    lib = store.open(library)
    marks = ','.join('?' * len(TEST_KINDS))
    columns, found = lib.sql(
        "SELECT id, kind, name, data, COALESCE(json_extract(data,'$.测试日'), json_extract(data,'$.日期'), json_extract(data,'$.导入时间'), "
        f"json_extract(data,'$.建立时间')) AS date FROM records WHERE deleted_at IS NULL AND kind IN ({marks}) AND date IS NOT NULL ORDER BY date DESC LIMIT 7", TEST_KINDS)
    recent = [_preview(lib, {**dict(zip(columns, item)), 'data': json.loads(item[3])}) for item in found]
    projects = lib.rows('课题') if lib.kind('课题') else []
    folder = config.outputs_dir(library)
    files = sorted((path for path in folder.iterdir() if path.is_file()), key=lambda path: -path.stat().st_mtime)[:6]
    return {'library': library, 'projects': projects, 'recent': recent, 'inbox': loop.inbox.view(), 'health': _health_counts(lib),
            'outputs': [{'name': path.name, 'size': path.stat().st_size, 'modified': path.stat().st_mtime} for path in files],
            'counts': {item['name']: item['records'] for item in lib.kinds()}, 'activity': _activity(lib), 'agent': _agent_summary()}


def _warm():
    """服务一启动就在后台把算法库（pandas、scipy、椭偏、GPC、n/k）和树脂核心先装好、常用的先算一遍，
    第一次打开椭偏、GPC、合成页不用等十几秒。"""
    import time
    started = time.time()
    lib = store.open(config.settings()['library'])
    from .sci import gpc, nk  # noqa: F401  装进来就够了
    days = sorted({row.get('测试日') for row in lib.rows('膜') if row.get('测试日')}, reverse=True)
    for day in days:                             # 每天的椭偏都先算好（全库的分组统计、配对、质量提示算一次，各天共用；每组的参比判定也留着）
        interact.ell_day(lib.name, day)
    present.experiments(lib.name)
    _health_counts(lib)
    print(f'预热完成：{time.time() - started:.0f} 秒', flush=True)


_HEALTH = {}


def _health_counts(lib):
    """总览上的"待关联""断链"数（旧平台首页有）。数据没变就用上次体检的数。"""
    stamp = lib.sql('SELECT max(updated_at), count(*) FROM records')[1][0]
    if _HEALTH.get(lib.name, (None,))[0] != stamp:
        from .sci import health
        counts = health.check(lib)['counts']
        _HEALTH[lib.name] = (stamp, {'待确认关联': counts.get('candidate_items', 0), '断链': counts.get('breaks', 0), '异常点': counts.get('outliers', 0)})
    return _HEALTH[lib.name][1]


def _activity(lib):
    """近 12 个月每月进来多少条数据，按分组（按记录自己的日期，不按导入时间）。"""
    groups = {item['name']: item['group'] or '其它' for item in lib.kinds()}
    _, found = lib.sql("SELECT kind, COALESCE(json_extract(data,'$.测试日'), json_extract(data,'$.日期'), json_extract(data,'$.导入时间'), "
                       "json_extract(data,'$.建立时间')) AS date FROM records WHERE deleted_at IS NULL AND date IS NOT NULL")
    months = {}
    for kind, date in found:
        month = str(date).replace('/', '-').replace('.', '-')[:7]
        if len(month) == 7 and month[4] == '-':
            bucket = months.setdefault(month, {})
            bucket[groups.get(kind, '其它')] = bucket.get(groups.get(kind, '其它'), 0) + 1
    keys = sorted(months)[-12:]
    return [{'month': key, **months[key]} for key in keys]


def _agent_summary():
    skills = insight.skills()
    days = {}
    for run in kernel.runs(500):
        day = (run.get('created') or '')[:10]
        if day:
            days.setdefault(day, {'done': 0, 'failed': 0, 'other': 0})
            days[day]['done' if run['status'] == 'done' else 'failed' if run['status'] == 'failed' else 'other'] += 1
    lanes = kernel.lanes()
    return {'skills': len(skills), 'mine': [item for item in skills if item['source'] != '内置'][:5],
            'top_skills': sorted([item for item in skills if item['uses']], key=lambda item: -item['uses'])[:5],
            'tools': len(tools.definitions()) + len(loop.KERNEL_TOOLS), 'mcp': [{'name': item['name'], 'state': item['state'], 'tools': len(item['tools'])} for item in mcp.hub.view()],
            'runs': [{'day': day, **days[day]} for day in sorted(days)[-14:]], 'lanes': len(lanes),
            'cost': round(sum(loop.lanes.get(item['id']).data['usage']['cost'] for item in lanes[:200]), 4), 'notes': len(list(config.notes_dir().glob('*.md'))) if config.notes_dir().is_dir() else 0}


# ---------- 产出、笔记、技能、页面 ----------

@app.get('/api/outputs/{library}')
def outputs(library: str):
    folder = config.outputs_dir(library)
    attached = {}
    for item in kernel.lanes():
        for output in loop.lanes.get(item['id']).data['outputs'] if item['id'] in loop.lanes.items else []:
            attached[output['file']] = output
    return {'files': [{'name': path.name, 'size': path.stat().st_size, 'modified': path.stat().st_mtime, **attached.get(path.name, {})}
                      for path in sorted(folder.iterdir(), key=lambda item: -item.stat().st_mtime) if path.is_file()]}


@app.get('/api/outputs/{library}/{name}')
def output_file(library: str, name: str):
    target = config.outputs_dir(library) / name
    if not target.is_file():
        raise HTTPException(404, '没有这个文件')
    return _send_file(target)


@app.get('/api/outputs/{library}/{name}/preview')
def output_preview(library: str, name: str):
    """Excel、CSV、Word 在页面上直接看：表格原样排出来（Excel 取算好的值），Word 出段落和表。"""
    return tabular.preview_file(config.outputs_dir(library) / name)


@app.post('/api/preview-bytes')
async def preview_bytes(name: str, request: Request):
    """导出按钮先预览：页面把刚生成的文件送回来，排成表和段落（不存进产出）。"""
    folder = config.HOME / '导出预览'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('预览' + Path(name).suffix.lower())
    path.write_bytes(await request.body())
    return tabular.preview_file(path)


@app.get('/api/texts/{area}')
def texts(area: str):
    folder, suffix = TEXTS[area][0](), TEXTS[area][1]
    return {'items': [{'name': path.stem, 'modified': path.stat().st_mtime} for path in sorted(folder.glob('*' + suffix))] if folder.is_dir() else []}


@app.get('/api/texts/{area}/{name}')
def text_read(area: str, name: str):
    path = TEXTS[area][0]() / (name + TEXTS[area][1])
    if not path.is_file():
        raise HTTPException(404, '没有这一篇')
    return {'name': name, 'content': path.read_text(encoding='utf-8')}


@app.put('/api/texts/{area}/{name}')
def text_write(area: str, name: str, data: dict = Body(...)):
    folder = TEXTS[area][0]()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + TEXTS[area][1])).write_text(data.get('content') or '', encoding='utf-8')
    return {'ok': True}


@app.get('/api/skills')
def skills():
    return {'skills': insight.skills()}


@app.get('/api/skills/{key}')
def skill_read(key: str):
    return insight.skill(key)


@app.put('/api/skills/{key}')
def skill_write(key: str, data: dict = Body(...)):
    return insight.save_skill(key, data['text'])


@app.delete('/api/skills/{key}')
def skill_drop(key: str):
    return insight.drop_skill(key)


# ---------- 智能体全貌：工具、MCP、上下文、跑过的轮 ----------

@app.get('/api/tools')
def tool_list():
    return {'tools': insight.tool_list()}


@app.get('/api/mcp')
def mcp_view():
    return {'servers': mcp.hub.view(), 'config': _masked(mcp.load()['servers'])}


@app.put('/api/mcp')
def mcp_save(data: dict = Body(...)):
    servers = data.get('servers') or data.get('mcpServers') or {}
    old = mcp.load()['servers']
    for name, spec in servers.items():          # 界面上看到的密钥是打码的：没改就沿用原来的
        for part in ('env', 'headers'):
            for key, value in (spec.get(part) or {}).items():
                if value == MASK:
                    spec[part][key] = (old.get(name, {}).get(part) or {}).get(key, '')
    mcp.hub.save(servers)
    return mcp_view()


@app.post('/api/mcp/{name}/restart')
def mcp_restart(name: str):
    return mcp.hub.restart(name)


MASK = '••••••'


def _masked(servers):
    return {name: {**spec, **{part: {key: MASK for key in (spec.get(part) or {})} for part in ('env', 'headers') if spec.get(part)}}
            for name, spec in servers.items()}


@app.get('/api/lanes/{lane_id}/context')
def lane_context(lane_id: str):
    return insight.context(lane_id)


@app.get('/api/agent/runs')
def agent_runs(limit: int = 200):
    return {'runs': insight.runs(limit)}


@app.get('/pages/{name}')
def page(name: str):
    path = config.pages_dir() / name
    if not path.is_file():
        raise HTTPException(404, '没有这个页面')
    return Response(path.read_text(encoding='utf-8'), media_type='text/html', headers=FRESH)


def main():
    """正式服务（<工作台目录>）用 pythonw 在后台跑，没有窗口：输出写进 LAB_LOG 指的日志文件。
    两个口：LAB_PORT 本机管理端（只有这台电脑能开，不用登录）；LAB_PUBLIC_PORT 外网访问口（内网穿透指向它，要登录）。"""
    import socket
    import uvicorn
    log = os.environ.get('LAB_LOG')
    if log:
        sys.stdout = sys.stderr = open(log, 'a', encoding='utf-8', buffering=1)
    else:
        for stream_ in (sys.stdout, sys.stderr):
            stream_.reconfigure(encoding='utf-8')
    port = int(os.environ.get('LAB_PORT') or 8850)
    public = int(os.environ.get('LAB_PUBLIC_PORT') or 0)
    with socket.socket() as probe:                   # 已经在跑了就不再起第二份（两份会各跑一套定时规则，写同一个库）
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            print(f'{store.now()} 研发工作台已经在运行：http://127.0.0.1:{port}', flush=True)
            return
    from . import seed
    seed.run()
    mcp.hub.start()
    loop.engine.start()
    threading.Thread(target=_warm, daemon=True, name='lab-warm').start()
    print(f'{store.now()} 研发工作台：本机 http://127.0.0.1:{port}' + (f'，外网访问口 http://127.0.0.1:{public}' if public else ''), flush=True)
    servers = [uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))]
    if public:
        servers.append(uvicorn.Server(uvicorn.Config(app, host=os.environ.get('LAB_PUBLIC_HOST') or '127.0.0.1', port=public, log_level='warning', lifespan='off')))
    async def serve_all():
        await asyncio.gather(*(server.serve() for server in servers))
    asyncio.run(serve_all())


if __name__ == '__main__':
    main()
