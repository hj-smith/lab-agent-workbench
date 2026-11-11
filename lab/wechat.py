"""微信推送：用微信官方的公众号接口（先用"公众平台测试号"，免费、微信自己的服务；以后换成认证服务号，接口一样）。

一次性准备（管理员做，设置 → 微信推送里有一步步的说明）：
  1. 用微信扫码登录公众平台测试号页面，抄下 appID、appsecret 填进来（appsecret 存 auth.json）；
  2. 测试号页面"接口配置信息"填 URL = <外网地址>/wechat/callback、Token = 这里给的那串，提交（微信会来验一次）；
  3. "模板消息接口"里新增一个模板，内容照这里给的那三行，抄下模板 ID 填进来。
每个人绑定：在"我的"页点"扫码绑定"→ 工作台向微信要一个带参数的临时二维码 → 他用微信扫、关注测试号 →
微信把"谁扫了哪个码"推到 /wechat/callback → 记下他的 openid（管理员的存设置，别人的存 用户.db），回他一句"绑定好了"。
外网地址还没配好、微信推不过来时：看关注列表里新出现的那一个（一次只绑一个人时准）。

推出去的：模板消息（标题、一两句话、时间，点开是工作台的链接，要登录）。
"""
import hashlib
import json
import secrets
import threading
import time
import xml.etree.ElementTree as ET

from . import config, people

API = 'https://api.weixin.qq.com/cgi-bin'
SECRET = 'wechat-secret'
TEMPLATE = '{{title.DATA}}\n内容：{{text.DATA}}\n时间：{{time.DATA}}'
pending = {}                     # 绑定码 → {owner, at, followers（开始时的关注列表）, openid}
_token = {'value': '', 'until': 0}
_lock = threading.Lock()


def conf():
    """{appid, template, token}：appid、模板 ID、回调 Token 存设置；appsecret 存 auth.json。"""
    value = (config.settings().get('wechat') or {})
    if not value.get('token'):
        value = {**value, 'token': secrets.token_hex(12)}
        save_conf(value)
    return value


def save_conf(value):
    settings = config.settings()
    config.save_settings({**settings, 'wechat': {key: value.get(key, '') for key in ('appid', 'template', 'token')}})


def ready():
    value = conf()
    return bool(value.get('appid') and value.get('template') and config.key_for(SECRET))


def _get(path, **params):
    import httpx
    return httpx.get(API + path, params=params, timeout=15).json()


def _post(path, body):
    import httpx
    return httpx.post(f'{API}{path}?access_token={access_token()}', content=json.dumps(body, ensure_ascii=False).encode(), timeout=15).json()


def _check(data, doing):
    if data.get('errcode') not in (None, 0):
        hint = {40013: 'appID 不对', 40125: 'appsecret 不对', 40001: 'appsecret 不对或过期', 40037: '模板 ID 不对', 43004: '他还没关注测试号',
                40003: '这个人的微信标识不对（要重新绑定）', 45009: '今天的接口次数用完了'}.get(data['errcode'], '')
        raise people.Problem(f'微信说{doing}没成：{hint or data.get("errmsg")}（{data["errcode"]}）', 502)
    return data


def access_token(fresh=False):
    with _lock:
        if fresh or not _token['value'] or _token['until'] < time.time() + 60:
            value = conf()
            data = _check(_get('/token', grant_type='client_credential', appid=value.get('appid'), secret=config.key_for(SECRET)), '取接口凭证')
            _token.update(value=data['access_token'], until=time.time() + int(data.get('expires_in') or 7200))
        return _token['value']


def check():
    """填好之后点"检查"：能拿到接口凭证就说明 appID、appsecret 对。"""
    access_token(fresh=True)
    return {'ok': True, 'followers': len(followers())}


def followers():
    out, after = [], ''
    while True:
        data = _check(_get('/user/get', access_token=access_token(), next_openid=after), '读关注列表')
        out += (data.get('data') or {}).get('openid') or []
        after = data.get('next_openid') or ''
        if not after or len(out) >= int(data.get('total') or 0):
            return out


# ---------- 扫码绑定 ----------

def bind_code(who):
    """要一个 30 分钟的带参数二维码。返回 {code, qr}：qr 是二维码里的那串地址，页面自己画成码。"""
    if not ready():
        raise people.Problem('管理员还没开通微信推送（设置 → 微信推送）。')
    code = secrets.token_urlsafe(9)
    data = _check(_post('/qrcode/create', {'expire_seconds': 1800, 'action_name': 'QR_STR_SCENE', 'action_info': {'scene': {'scene_str': 'bind:' + code}}}), '生成二维码')
    try:
        known = followers()
    except people.Problem:
        known = None
    pending[code] = {'owner': who['id'] or 'admin', 'at': time.time(), 'followers': set(known) if known is not None else None, 'openid': ''}
    return {'code': code, 'qr': data['url'], 'expires_in': 1800}


def poll(code):
    """页面每 3 秒问一次：微信推过来的扫码事件（准）；推不过来时，看关注列表里新出现的那一个。"""
    item = pending.get(code)
    if not item or time.time() - item['at'] > 1800:
        raise people.Problem('这个二维码过期了，重新点"扫码绑定"。', 404)
    if not item['openid'] and item['followers'] is not None and time.time() - item.get('looked', 0) > 6:
        item['looked'] = time.time()
        fresh = [openid for openid in followers() if openid not in item['followers']]
        if len(fresh) == 1 and sum(1 for other in pending.values() if not other['openid'] and other is not item and time.time() - other['at'] < 1800) == 0:
            item['openid'] = fresh[0]
    if not item['openid']:
        return False
    save_uid(item['owner'], item['openid'])
    pending.pop(code, None)
    return True


def callback_verify(signature, timestamp, nonce):
    expected = hashlib.sha1(''.join(sorted([conf()['token'], str(timestamp), str(nonce)])).encode()).hexdigest()
    return bool(signature) and secrets.compare_digest(expected, str(signature))


def callback_event(body):
    """微信推来的消息（XML）。扫了绑定码（关注时 EventKey 带 qrscene_ 前缀）就记下 openid，回他一句。返回要回给微信的 XML（空 = 不回）。"""
    root = ET.fromstring(body)
    get = lambda tag: (root.findtext(tag) or '').strip()
    openid, event, key = get('FromUserName'), get('Event').lower(), get('EventKey')
    if get('MsgType') != 'event' or event not in ('subscribe', 'scan'):
        return ''
    code = key.removeprefix('qrscene_').removeprefix('bind:') if 'bind:' in key else ''
    item = pending.get(code)
    if not item:
        return _reply(get('ToUserName'), openid, '关注好了。要收研发工作台的通知，在工作台"我的"页点"扫码绑定"，扫那里的码。' if event == 'subscribe' else '')
    item['openid'] = openid
    return _reply(get('ToUserName'), openid, '绑定好了：以后要你拍板的事、助手的通知会推到这里。')


def _reply(me, to, text):
    if not text:
        return ''
    return (f'<xml><ToUserName><![CDATA[{to}]]></ToUserName><FromUserName><![CDATA[{me}]]></FromUserName><CreateTime>{int(time.time())}</CreateTime>'
            f'<MsgType><![CDATA[text]]></MsgType><Content><![CDATA[{text}]]></Content></xml>')


def save_uid(owner, openid):
    if owner == 'admin':
        value = config.settings()
        config.save_settings({**value, 'push': {**(value.get('push') or {}), 'wechat_uid': openid or ''}})
    else:
        with people._lock, people._db() as db:
            db.execute('UPDATE people SET wechat_uid=? WHERE id=?', (openid or '', owner))


def uid_of(person_id):
    if not person_id:
        return (config.settings().get('push') or {}).get('wechat_uid') or ''
    row = people._row('id=?', person_id)
    return row['wechat_uid'] if row else ''


def link(href):
    base = (config.settings().get('public_url') or (config.settings().get('push') or {}).get('link') or '').rstrip('/')
    return f'{base}/{(href or "").lstrip("/")}' if base else ''


def send(uids, title, text='', href=''):
    """模板消息推给这些人。返回 (成没成, 说明)。"""
    uids = [uid for uid in uids if uid]
    if not ready() or not uids:
        return False, '没开通微信推送，或这个人还没绑定。'
    from .store import now
    url, problems = link(href), []
    for uid in uids:
        try:
            _check(_post('/message/template/send', {'touser': uid, 'template_id': conf()['template'], **({'url': url} if url else {}),
                                                    'data': {'title': {'value': title[:60]}, 'text': {'value': (text or '')[:180]}, 'time': {'value': now()[:16]}}}), '推送')
        except Exception as error:
            problems.append(str(error))
    return (not problems), ('推到微信了。' if not problems else '；'.join(problems))
