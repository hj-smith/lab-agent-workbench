"""桌面通知：你不在工作台前的时候，要你拍板的卡片、助手的通知在 Windows 右下角弹一下，点了直接打开那一页。

"在不在跟前"：工作台页面在前台时每 20 秒报一声（/api/presence），45 秒内报过就算在，不弹。
用系统自带的 Windows 通知（PowerShell 调 Windows.UI.Notifications），不装任何东西；文字用 -EncodedCommand 传，中文不乱码。
设置里"桌面通知"能分别关掉卡片和通知。
"""
import base64
import re
import subprocess
import threading
import time
from urllib.parse import quote
from xml.sax.saxutils import escape

from . import config

SEEN = {'at': 0.0, 'base': 'http://localhost:8850/'}
PHONE_KEY = 'push-phone'          # auth.json 里：他自己建的群机器人地址或推送地址（含密钥，不进 settings.json）
APP = r'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'    # 系统自带的发通知身份，不用注册


def presence(base=None):
    SEEN['at'] = time.time()
    if base:
        SEEN['base'] = base


def away():
    return time.time() - SEEN['at'] > 45


def wanted(kind):
    return (config.settings().get('push') or {}).get(kind, True)


def toast(kind, title, text='', href='', force=False, owner=''):
    """kind：卡片 / 通知。owner：这段对话是谁的（空 = 管理员或系统）。
    别人的对话：推到他自己绑定的微信（他这两分钟没在用工作台时）。管理员的：他在工作台前、或者设置里关了这一类，就不弹；
    不在跟前时桌面弹一下，设了手机推送地址的推手机，绑了微信的推微信。"""
    from . import people, wechat
    if owner:
        if wanted(kind) and (force or time.time() - people.seen.get(owner, (0, ''))[0] > 120) and wechat.uid_of(owner):
            threading.Thread(target=wechat.send, args=([wechat.uid_of(owner)], title, text, href), daemon=True).start()
            return True
        return False
    if not force and (not wanted(kind) or not away()):
        return False
    if not force and (config.settings().get('push') or {}).get('phone', True) and config.key_for(PHONE_KEY):
        threading.Thread(target=phone, args=(title, text, href), daemon=True).start()
    if not force and wechat.uid_of(''):
        threading.Thread(target=wechat.send, args=([wechat.uid_of('')], title, text, href), daemon=True).start()
    url = SEEN['base'].rstrip('/') + '/' + (href or '').lstrip('/')
    xml = (f'<toast activationType="protocol" launch="{escape(url, {chr(34): "&quot;"})}">'
           f'<visual><binding template="ToastGeneric"><text>{escape(title[:80])}</text><text>{escape((text or "")[:200])}</text></binding></visual></toast>')
    script = ('[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
              '[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;'
              f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml(@'\n{xml}\n'@);"
              f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP}').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    threading.Thread(target=subprocess.run, args=(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],),
                     kwargs={'capture_output': True, 'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}, daemon=True).start()
    return True


# ---------- 手机推送 ----------
# 他自己建一个推送入口，把地址贴进来（存 auth.json）：企业微信 / 钉钉 / 飞书的群机器人 webhook，iPhone 的 Bark，ntfy 的主题地址，或 Server酱。
# 按地址认是哪一种，各发各的格式。"点开的地址"填了（例 NAS 上的 /lab/），消息里带一个能在手机上点开的链接。

def channel(url):
    host = re.sub(r'^https?://', '', str(url or '')).split('/')[0].lower()
    if 'qyapi.weixin.qq.com' in host:
        return '企业微信群机器人'
    if 'dingtalk' in host:
        return '钉钉群机器人'
    if 'feishu' in host or 'larksuite' in host:
        return '飞书群机器人'
    if 'ftqq.com' in host:
        return 'Server酱'
    if 'ntfy' in host:
        return 'ntfy'
    if 'day.app' in host or 'bark' in host:
        return 'Bark'
    return ''


def phone(title, text='', href=''):
    """推一条到手机。返回 (成没成, 说明)。"""
    import httpx
    url = config.key_for(PHONE_KEY)
    if not url:
        return False, '还没设手机推送的地址。'
    base = ((config.settings().get('push') or {}).get('link') or '').rstrip('/')
    link = f'{base}/m' + (f'?go={quote(href)}' if href else '') if base else ''
    kind, body = channel(url), (text or '').strip()
    try:
        if kind == '企业微信群机器人':
            reply = httpx.post(url, json={'msgtype': 'markdown', 'markdown': {'content': f'**{title}**\n{body}' + (f'\n[在手机上打开]({link})' if link else '')}}, timeout=15)
        elif kind == '钉钉群机器人':
            reply = httpx.post(url, json={'msgtype': 'markdown', 'markdown': {'title': title[:60], 'text': f'### {title}\n{body}' + (f'\n\n[在手机上打开]({link})' if link else '')}}, timeout=15)
        elif kind == '飞书群机器人':
            reply = httpx.post(url, json={'msg_type': 'text', 'content': {'text': f'{title}\n{body}' + (f'\n{link}' if link else '')}}, timeout=15)
        elif kind == 'Server酱':
            reply = httpx.post(url, data={'title': title[:32], 'desp': body + (f'\n\n[在手机上打开]({link})' if link else '')}, timeout=15)
        elif kind == 'ntfy':
            server, _, topic = url.rstrip('/').rpartition('/')
            reply = httpx.post(server + '/', json={'topic': topic, 'title': title, 'message': body or title, **({'click': link} if link else {})}, timeout=15)
        else:                                          # Bark 和别的：按 Bark 的 JSON 格式
            reply = httpx.post(url.rstrip('/'), json={'title': title, 'body': body or title, **({'url': link} if link else {}), 'group': '研发工作台'}, timeout=15)
    except httpx.HTTPError as error:
        return False, f'发不出去：{type(error).__name__} {error}'
    if reply.status_code >= 400:
        return False, f'对方返回 {reply.status_code}：{reply.text[:200]}'
    data = reply.json() if 'json' in reply.headers.get('content-type', '') else {}
    if data.get('errcode') not in (None, 0) or data.get('code') not in (None, 0, 200):     # 群机器人出错时 HTTP 也是 200，错在正文里
        return False, f'对方说没收下：{str(data)[:200]}'
    return True, f'发到了{kind or "推送地址"}。'
