"""用户和权限（设计见 workbench2\\设计-用户和权限.md）。

两个口：本机管理端（LAB_PORT，只有这台电脑能开，不用登录，就是管理员）；外网访问口（LAB_PUBLIC_PORT，内网穿透只指向它，
每个请求都要登录：账号 + 密码 + 验证器 6 位数）。
管理员（你）什么都能做，在外面登录后也一样，没有任何限制。别人按管理员给的权限：每个库（包括知识库）一个等级（不给 / 看 / 填 / 改），
再加三个开关：用助手、助手能动这台电脑、导出和下载。助手按"这段对话是谁的"那个人的权限干活。

人存在 数据\\用户.db；验证器的密钥用 数据\\用户.key 加密存（只拷走 用户.db 拿不到）。登录状态是签过名的 cookie，不另建表：
一次登录 8 小时；"记住这台设备"30 天只免输密码，验证码每次都要输。
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import struct
import threading
import time
import urllib.parse

from . import config

LEVELS = {'看': 1, '填': 2, '改': 3}
ROLES = {                         # 邀请时先选身份，再按人细改；"管理员"什么都有，以后新建的库也有
    '管理员': {'all': True, 'assistant': True, 'machine': True, 'export': True, 'home': '总览'},
    '研发工程师': {'level': '改', 'knowledge': '改', 'assistant': True, 'machine': False, 'export': True, 'home': '总览'},
    '实验员': {'level': '填', 'knowledge': '', 'assistant': False, 'machine': False, 'export': False, 'home': '现场'},
    '只看': {'level': '看', 'knowledge': '看', 'assistant': False, 'machine': False, 'export': False, 'home': '总览'},
}
USERNAME = re.compile(r'[\w.\-]{2,32}\Z')
INVITE_SECONDS = 7 * 24 * 3600
SESSION_SECONDS = 8 * 3600
REMEMBER_SECONDS = 30 * 24 * 3600
LOCAL = {'id': '', 'name': '管理员（本机）', 'username': '', 'role': '管理员', 'admin': True, 'rights': {'all': True, 'assistant': True, 'machine': True, 'export': True}}
SYSTEM = LOCAL                    # 定时规则的活按系统跑，全权限
_lock = threading.Lock()
seen = {}                         # 谁现在在线：person → (时间, 地址)，只在内存里


# ---------- 存储 ----------

def _db():
    db = sqlite3.connect(config.HOME / '用户.db', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS people(
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '只看', rights TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'invited',
        password_hash TEXT NOT NULL DEFAULT '', totp_secret TEXT NOT NULL DEFAULT '', totp_last_step INTEGER NOT NULL DEFAULT 0,
        recovery TEXT NOT NULL DEFAULT '[]', invite_hash TEXT NOT NULL DEFAULT '', invite_expires INTEGER NOT NULL DEFAULT 0,
        expires INTEGER NOT NULL DEFAULT 0, sessions_from INTEGER NOT NULL DEFAULT 0, last_login INTEGER NOT NULL DEFAULT 0,
        last_ip TEXT NOT NULL DEFAULT '', wechat_uid TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL)''')
    return db


def _key():
    path = config.HOME / '用户.key'
    if not path.is_file():
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def _stream(key, nonce, length):
    out, counter = b'', 0
    while len(out) < length:
        out += hmac.new(key, b'stream' + nonce + struct.pack('>I', counter), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def seal(text):
    key, nonce, data = _key(), secrets.token_bytes(16), text.encode()
    body = bytes(a ^ b for a, b in zip(data, _stream(key, nonce, len(data))))
    return base64.b64encode(nonce + hmac.new(key, b'tag' + nonce + body, hashlib.sha256).digest() + body).decode()


def unseal(sealed):
    key, raw = _key(), base64.b64decode(sealed)
    nonce, tag, body = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(key, b'tag' + nonce + body, hashlib.sha256).digest()):
        raise ValueError('密钥对不上')
    return bytes(a ^ b for a, b in zip(body, _stream(key, nonce, len(body)))).decode()


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def hash_password(password, rounds=240000):
    salt = secrets.token_bytes(16)
    return f'pbkdf2${rounds}${salt.hex()}${hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex()}'


def password_matches(password, encoded):
    try:
        _, rounds, salt, expected = encoded.split('$')
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', str(password).encode(), bytes.fromhex(salt), int(rounds)).hex(), expected)


def totp(secret, step):
    value = hmac.new(base64.b32decode(secret), struct.pack('>Q', step), hashlib.sha1).digest()
    offset = value[-1] & 15
    return '%06d' % ((struct.unpack('>I', value[offset:offset + 4])[0] & 0x7fffffff) % 1000000)


def matching_step(secret, code, now=None):
    """6 位数属于哪个 30 秒（前后各让一格，手机时间差一点也行），对不上返回 None。"""
    code = re.sub(r'\s', '', str(code or ''))
    if not re.fullmatch(r'\d{6}', code):
        return None
    step = int((time.time() if now is None else now) // 30)
    return next((candidate for candidate in (step, step - 1, step + 1) if hmac.compare_digest(totp(secret, candidate), code)), None)


class Problem(Exception):
    def __init__(self, text, status=400):
        super().__init__(text)
        self.status = status


def _row(where, *values):
    with _db() as db:
        row = db.execute('SELECT * FROM people WHERE ' + where, values).fetchone()
    return dict(row) if row else None


def person(row):
    """一行 → 页面和权限检查用的人。"""
    rights = json.loads(row['rights'] or '{}')
    admin = row['role'] == '管理员'
    return {'id': row['id'], 'name': row['name'] or row['username'], 'username': row['username'], 'role': row['role'], 'admin': admin,
            'rights': {**ROLES['管理员'], 'libraries': {}} if admin else rights}


def public_row(row):
    rights = json.loads(row['rights'] or '{}')
    at, address = seen.get(row['id'], (0, ''))
    return {'id': row['id'], 'username': '' if row['username'].startswith('invite-') else row['username'], 'name': row['name'], 'note': row['note'],
            'role': row['role'], 'rights': rights, 'status': row['status'], 'expires': row['expires'], 'last_login': row['last_login'], 'last_ip': row['last_ip'],
            'online': time.time() - at < 120, 'invite_expires': row['invite_expires'] if row['status'] == 'invited' else 0,
            'wechat': bool(row['wechat_uid']), 'created': row['created']}


def usable(row):
    return row['status'] == 'active' and (not row['expires'] or row['expires'] > time.time())


def get(person_id):
    """对话主人 → 人。空 = 管理员（本机）或系统，全权限。人被删了、停用了，按没有权限的人算。"""
    if not person_id:
        return LOCAL
    row = _row('id=?', person_id)
    return person(row) if row and usable(row) else {'id': person_id, 'name': '（已停用）', 'username': '', 'role': '', 'admin': False, 'rights': {}}


# ---------- 管理：邀请、改权限、停用 ----------

def rights_for(role, libraries=None, **switches):
    """按身份给一套默认权限：libraries 是给哪些库（不给就是全部产品库），knowledge 是知识库的等级。"""
    base = ROLES.get(role) or ROLES['只看']
    if base.get('all'):
        return {}
    names = [name for name in config.libraries() if name != config.KNOWLEDGE]
    levels = {name: base['level'] for name in (libraries if libraries is not None else names)}
    if base['knowledge']:
        levels[config.KNOWLEDGE] = base['knowledge']
    return {'libraries': levels, 'assistant': base['assistant'], 'machine': base['machine'], 'export': base['export'], 'home': base['home'], **switches}


def _clean_rights(rights):
    rights = dict(rights or {})
    rights['libraries'] = {str(name): level for name, level in (rights.get('libraries') or {}).items() if level in LEVELS}
    for key in ('assistant', 'machine', 'export'):
        rights[key] = bool(rights.get(key))
    rights['home'] = rights.get('home') if rights.get('home') in ('总览', '现场') else '总览'
    return rights


def people():
    with _db() as db:
        return [public_row(dict(row)) for row in db.execute('SELECT * FROM people ORDER BY created')]


def invite(name='', note='', role='研发工程师', rights=None, expires=0):
    role = role if role in ROLES else '只看'
    token, now = secrets.token_urlsafe(32), int(time.time())
    person_id = 'p-' + secrets.token_hex(6)
    with _lock, _db() as db:
        db.execute('INSERT INTO people(id, username, name, note, role, rights, invite_hash, invite_expires, expires, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (person_id, 'invite-' + secrets.token_hex(6), str(name or '').strip(), str(note or '').strip(), role,
                    json.dumps(_clean_rights(rights if rights is not None else rights_for(role)), ensure_ascii=False), digest(token), now + INVITE_SECONDS,
                    int(expires or 0), now, now))
    return {'person': public_row(_row('id=?', person_id)), 'token': token}


def reinvite(person_id):
    """忘了密码、换了手机：发一个新邀请，重设密码、重新扫验证器；他已登录的设备都退出。"""
    token, now = secrets.token_urlsafe(32), int(time.time())
    with _lock, _db() as db:
        db.execute("UPDATE people SET status='invited', password_hash='', totp_secret='', recovery='[]', invite_hash=?, invite_expires=?, sessions_from=?, updated=? WHERE id=?",
                   (digest(token), now + INVITE_SECONDS, now + 1, now, person_id))
    return {'person': public_row(_row('id=?', person_id)), 'token': token}


def update(person_id, data):
    row = _row('id=?', person_id)
    if not row:
        raise Problem('没有这个人。', 404)
    role = data.get('role', row['role'])
    rights = _clean_rights(data['rights']) if 'rights' in data else json.loads(row['rights'] or '{}')
    status = row['status']
    if 'enabled' in data:
        status = ('active' if row['password_hash'] else 'invited') if data['enabled'] else 'disabled'
    now = int(time.time())
    with _lock, _db() as db:
        db.execute('UPDATE people SET role=?, rights=?, status=?, name=?, note=?, expires=?, updated=? WHERE id=?',
                   (role if role in ROLES else row['role'], json.dumps(rights, ensure_ascii=False), status, str(data.get('name', row['name'])).strip(),
                    str(data.get('note', row['note'])).strip(), int(data.get('expires', row['expires']) or 0), now, person_id))
    return public_row(_row('id=?', person_id))


def kick(person_id):
    with _lock, _db() as db:
        db.execute('UPDATE people SET sessions_from=? WHERE id=?', (int(time.time()) + 1, person_id))


def remove(person_id):
    with _lock, _db() as db:
        db.execute('DELETE FROM people WHERE id=?', (person_id,))


def admins_exist():
    with _db() as db:
        return bool(db.execute("SELECT 1 FROM people WHERE role='管理员' AND status='active'").fetchone())


# ---------- 被邀请的人：设账号密码、绑验证器 ----------

def invite_row(token):
    row = _row('invite_hash=?', digest(token)) if token else None
    if not row or row['status'] != 'invited' or row['invite_expires'] < time.time():
        raise Problem('邀请链接无效或已过期，请找管理员要一个新的。', 404)
    return row


def invite_start(token):
    row = invite_row(token)
    if row['totp_secret']:
        secret = unseal(row['totp_secret'])
    else:
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        with _lock, _db() as db:
            db.execute('UPDATE people SET totp_secret=? WHERE id=?', (seal(secret), row['id']))
    label = urllib.parse.quote(f'研发工作台:{row["name"] or "新用户"}')
    return {'name': row['name'], 'username': '' if row['username'].startswith('invite-') else row['username'], 'role': row['role'], 'secret': secret,
            'uri': f'otpauth://totp/{label}?secret={secret}&issuer={urllib.parse.quote("研发工作台")}&digits=6&period=30'}


def invite_finish(token, name, username, password, code):
    row = invite_row(token)
    name, username = str(name or '').strip(), str(username or '').strip()
    if not name:
        raise Problem('写一下你的名字（同事看到的就是它）。')
    if not USERNAME.fullmatch(username) or username.startswith('invite-'):
        raise Problem('账号用 2 到 32 个汉字、字母、数字、点、横线或下划线。')
    if len(str(password or '')) < 8:
        raise Problem('密码至少 8 位。')
    step = matching_step(unseal(row['totp_secret']), code) if row['totp_secret'] else None
    if step is None:
        raise Problem('验证码不对：看验证器里这个账号最新的 6 位数，手机时间要准。')
    recovery = ['-'.join(secrets.token_hex(2) for _ in range(3)) for _ in range(8)]
    try:
        with _lock, _db() as db:
            db.execute("UPDATE people SET status='active', username=?, name=?, password_hash=?, totp_last_step=?, recovery=?, invite_hash='', invite_expires=0, updated=? WHERE id=?",
                       (username, name, hash_password(password), step, json.dumps([digest(item) for item in recovery]), int(time.time()), row['id']))
    except sqlite3.IntegrityError:
        raise Problem('这个账号已经有人用了，换一个。', 409) from None
    return {'person': person(_row('id=?', row['id'])), 'recovery': recovery}


# ---------- 登录 ----------

def authenticate(username, password, code, remembered=''):
    """账号 + 密码 + 验证码（这台设备记住了这个人就免密码）。验证码也可以用一个备用码。错哪一样都只说"不对"。"""
    wrong = Problem('账号、密码或验证码不对。', 401)
    row = _row('username=?', str(username or '').strip()) if username else None
    skip = bool(row and remembered and remembered == row['id'])
    matched = skip or password_matches(password or '', row['password_hash'] if row and row['password_hash'] else hash_password('x', 1000))
    if not row or not matched or not usable(row):
        time.sleep(0.8)
        raise wrong
    step = matching_step(unseal(row['totp_secret']), code)
    if step is not None and step > row['totp_last_step']:
        change = ('totp_last_step=?', step)
    else:
        codes = json.loads(row['recovery'] or '[]')
        used = digest(re.sub(r'\s', '', str(code or '')).lower())
        if used not in codes:
            time.sleep(0.8)
            raise wrong
        codes.remove(used)
        change = ('recovery=?', json.dumps(codes))
    with _lock, _db() as db:
        db.execute(f'UPDATE people SET {change[0]} WHERE id=?', (change[1], row['id']))
    return person(row)


def change_password(person_id, old, new, code):
    row = _row('id=?', person_id)
    if not row or not password_matches(old or '', row['password_hash']) or matching_step(unseal(row['totp_secret']), code) is None:
        raise Problem('原密码或验证码不对。', 401)
    if len(str(new or '')) < 8:
        raise Problem('新密码至少 8 位。')
    with _lock, _db() as db:
        db.execute('UPDATE people SET password_hash=?, updated=? WHERE id=?', (hash_password(new), int(time.time()), person_id))


def recovery_left(person_id):
    row = _row('id=?', person_id)
    return len(json.loads(row['recovery'] or '[]')) if row else 0


def record_login(person_id, address):
    with _lock, _db() as db:
        db.execute('UPDATE people SET last_login=?, last_ip=? WHERE id=?', (int(time.time()), str(address)[:64], person_id))


def sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    return body + '.' + hmac.new(_key(), b'cookie' + body.encode(), hashlib.sha256).hexdigest()[:40]


def unsign(value):
    try:
        body, mark = str(value or '').rsplit('.', 1)
    except ValueError:
        return None
    if not hmac.compare_digest(mark, hmac.new(_key(), b'cookie' + body.encode(), hashlib.sha256).hexdigest()[:40]):
        return None
    payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    return payload if payload.get('e', 0) > time.time() else None


def session_cookie(person_id):
    now = int(time.time())
    return sign({'p': person_id, 't': now, 'e': now + SESSION_SECONDS})


def remember_cookie(person_id):
    now = int(time.time())
    return sign({'r': person_id, 't': now, 'e': now + REMEMBER_SECONDS})


def from_session(value, address=''):
    """cookie → 人；过期、被踢下线、停用、过了有效期都返回 None。"""
    payload = unsign(value)
    if not payload or not payload.get('p'):
        return None
    row = _row('id=?', payload['p'])
    if not row or not usable(row) or payload['t'] < row['sessions_from']:
        return None
    seen[row['id']] = (time.time(), address)
    return person(row)


def remembered(value):
    payload = unsign(value)
    row = _row('id=?', payload['r']) if payload and payload.get('r') else None
    return {'id': row['id'], 'username': row['username'], 'name': row['name']} if row and usable(row) and payload['t'] >= row['sessions_from'] else None


# ---------- 权限 ----------

def level(who, library):
    if who['admin'] or (who['rights'] or {}).get('all'):
        return 3
    return LEVELS.get(((who['rights'] or {}).get('libraries') or {}).get(library or ''), 0)


def switch(who, name):
    return who['admin'] or bool((who['rights'] or {}).get(name))


def libraries(who):
    names = config.libraries()
    return names if who['admin'] else [name for name in names if level(who, name)]


# 接口按处理函数的名字分：没列出来的，GET 要"看"、别的要"改"
FILL = {'order_weigh', 'paper_save', 'paper_import', 'import_upload', 'import_inspect', 'import_commit', 'aging_upload', 'aging_commit',
        'importq_upload', 'importq_ping', 'attach_file', 'intake_go', 'mobile_photo'}
COMPUTE = {'gpc_try', 'gpc_batch_preview', 'gpc_batch_compare', 'gpc_cal_trial', 'gpc_cal_compare', 'thickness_analyse', 'formula_calc', 'premix',
           'sql', 'surrogate_propose', 'gpc_align'}                                   # POST 但只算不存：要"看"
DOWNLOAD = {'export', 'paper_export', 'order_export', 'gpc_report', 'gpc_figure', 'output_file'}
ADMIN = {'openapi', 'swagger_ui_html', 'swagger_ui_redirect', 'redoc_html', 'save_settings', 'save_key', 'models', 'runs', 'get_rules', 'put_rules',
         'present_layout', 'literature_topics', 'literature_check', 'mcp_server', 'mcp_stream', 'prompt_view', 'prompt_save', 'push_phone_test',
         'push_test', 'intake_scan', 'team_save', 'rule_run', 'preview_bytes', 'texts', 'text_read', 'text_write', 'skills', 'skill_read',
         'skill_write', 'skill_drop', 'tool_list', 'mcp_view', 'mcp_save', 'mcp_restart', 'agent_runs', 'page',
         'users_list', 'users_invite', 'users_update', 'users_reinvite', 'users_kick', 'users_remove', 'wechat_setup', 'wechat_check'}
KNOWLEDGE_READ = {'literature_view', 'literature_find', 'learn_view', 'learn_create', 'learn_detail', 'learn_answer'}
KNOWLEDGE_WRITE = {'literature_lead', 'literature_pdf'}
ANY = {'index', 'mobile', 'web_file', 'state', 'stream', 'presence', 'lanes', 'auth_me', 'auth_logout', 'auth_password', 'auth_logout_others',
       'wechat_me', 'wechat_bind', 'wechat_poll', 'wechat_unbind', 'wechat_test', 'wechat_callback_check', 'wechat_callback'}
ASSISTANT = {'say', 'team_view', 'team_task'}
LANE = {'lane', 'answer', 'rewind_lane', 'fork_lane', 'close_card', 'stop', 'delete_lane', 'cancel_timer', 'upload', 'workspace_file', 'lane_context'}
LANE_READ = {'lane', 'workspace_file', 'lane_context'}
LANE_ANSWER = {'answer', 'close_card'}


def route_problem(who, endpoint, method, params):
    """这个人能不能调这个接口：能返回 None，不能返回一句话。管理员什么都能。对话（lane_id）的再按对话主人查，见 lane_problem。"""
    if who['admin']:
        return None
    if endpoint in ADMIN:
        return '这一项只有管理员能用。'
    if endpoint in ANY or endpoint in LANE:
        return None
    if endpoint in ASSISTANT:
        return None if switch(who, 'assistant') else '你还没有用助手的权限，找管理员开。'
    if endpoint in KNOWLEDGE_READ:
        return None if level(who, config.KNOWLEDGE) >= 1 else '你还没有知识库的权限。'
    if endpoint in KNOWLEDGE_WRITE:
        return None if level(who, config.KNOWLEDGE) >= 3 else '知识库你只能看，不能改。'
    library = params.get('library')
    if library is None:
        return '这一项只有管理员能用。'
    have = level(who, library)
    if not have:
        return f'你还没有「{library}」的权限。'
    if endpoint in DOWNLOAD and not switch(who, 'export'):
        return '你还没有导出和下载的权限。'
    need = 1 if method in ('GET', 'HEAD') or endpoint in COMPUTE else 2 if endpoint in FILL else 3
    if have < need:
        return f'「{library}」你只能{"看" if have == 1 else "看和现场填写"}，这一步要{"填写" if need == 2 else "改"}的权限。'
    return None


# 助手的工具：动这台电脑的、写库的、只读的
MACHINE_TOOLS = {'python', 'pip', 'shell', 'files', 'extend'}
WRITE_ACTIONS = {'records': {'define', 'save', 'delete', 'restore', 'link'}, 'notes': {'write', 'append'}, 'literature': {'check', 'screen', 'decide'},
                 'outputs': {'attach'}, 'demos': {'used'}, 'cards': {'close'}}
ALWAYS_WRITE = {'brief'}
KNOWLEDGE_TOOLS = {'notes', 'literature', 'lesson'}


def tool_problem(owner, library, name, args):
    """助手在某人的对话里用工具：按这个人的权限。能用返回 None，不能返回给助手看的一句话（它会换个做法或者告诉那个人）。"""
    who = get(owner)
    if who['admin']:
        return None
    args = args or {}
    if name.startswith('mcp__') or name in MACHINE_TOOLS:
        return f'这段对话是{who["name"]}的，他没有"助手能动这台电脑"的权限：{name} 不能用。只用库里的数据和算法回答，或者告诉他找管理员。'
    target = config.KNOWLEDGE if name in KNOWLEDGE_TOOLS else (args.get('library') or library)
    have = level(who, target)
    if not have:
        return f'这段对话是{who["name"]}的，他没有「{target}」的权限。'
    writes = name in ALWAYS_WRITE or args.get('action') in WRITE_ACTIONS.get(name, ())
    if name == 'lesson':                    # 学习是他自己的练习：能看知识库就能做
        writes = False
    if writes and have < 3:
        return f'这段对话是{who["name"]}的，他在「{target}」只能{"看" if have == 1 else "看和现场填写"}：不能写进库。把结果写在回答里给他看。'
    if name == 'export' and not switch(who, 'export'):
        return f'这段对话是{who["name"]}的，他没有导出和下载的权限：不要做文件，直接在回答里给他看。'
    return None


def lane_visible(who, lane):
    """lane：{id, owner, rule, library}。自己的对话；定时规则的活按库给能看的人；管理员都能看。"""
    if who['admin']:
        return True
    if (lane.get('owner') or '') == who['id'] and who['id']:
        return True
    return bool(lane.get('rule')) and level(who, lane.get('library') or '') >= 1


def lane_answerable(who, lane):
    if who['admin'] or ((lane.get('owner') or '') == who['id'] and who['id']):
        return True
    return bool(lane.get('rule')) and level(who, lane.get('library') or '') >= 3


def lane_problem(who, endpoint, lane):
    """对话接口：看要能看到这段对话；回答卡片要能拍板；别的（接着说、回退、分叉、停、删、传文件）要是自己的对话。"""
    if who['admin']:
        return None
    if not lane_visible(who, lane):
        return '这段对话不是你的。'
    if endpoint in LANE_READ:
        return None
    if endpoint in LANE_ANSWER:
        return None if lane_answerable(who, lane) else '这件事要在这个库有"改"权限的人拍板。'
    if not switch(who, 'assistant'):
        return '你还没有用助手的权限，找管理员开。'
    return None if (lane.get('owner') or '') == who['id'] else '这段对话不是你的，只能看。'
