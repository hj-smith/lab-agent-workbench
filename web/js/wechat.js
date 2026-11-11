// 微信推送：用微信官方的公众号接口（先用"公众平台测试号"，免费）。管理员做一次准备；之后每个人扫一个码就绑上自己的微信。
import {api, h, icon, toast} from './core.js';

const SANDBOX = 'https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login';
const copy = (text) => h('button.btn.ghost.small', {type: 'button', onclick: async () => { await navigator.clipboard.writeText(text).catch(() => {}); toast('复制好了'); }}, '复制');

async function qrLib() {
  if (!window.qrcode) await new Promise((resolve) => { const s = h('script', {src: '/web/vendor/qrcode.js'}); s.onload = resolve; document.head.append(s); });
  return window.qrcode;
}

// 管理员的准备：测试号 appID、appsecret、回调地址和 Token、模板
function setupBox(data, done) {
  const appid = h('input', {type: 'text', value: data.appid || '', placeholder: 'wx 开头的一串', autocomplete: 'off'});
  const secret = h('input', {type: 'password', placeholder: data.has_secret ? '已经填过（不换就空着）' : '测试号页面上的 appsecret', autocomplete: 'off'});
  const template = h('input', {type: 'text', value: data.template || '', placeholder: '新增模板后页面上给的"模板ID"', autocomplete: 'off'});
  const status = h('span.small');
  return h('div.wx-setup', {},
    h('p', {}, '用微信自己的公众号接口推送。先用"公众平台测试号"：免费，不用注册公司，最多 100 人关注。以后换认证服务号，接口一样。准备一次（只有管理员做）：'),
    h('ol.wx-steps', {},
      h('li', {}, '电脑上打开 ', h('a', {href: SANDBOX, target: '_blank', rel: 'noopener'}, '微信公众平台测试号'), '，用微信扫码登录。'),
      h('li', {}, '把页面最上面的 ', h('b', {}, 'appID'), '、', h('b', {}, 'appsecret'), ' 填到下面。'),
      h('li', {}, '页面上"接口配置信息"点修改，填：',
        h('div.wx-kv', {}, h('span', {}, 'URL'), data.callback ? [h('code', {}, data.callback), copy(data.callback)]
          : h('span.quiet', {}, '要先在 设置 → 用户和权限 填"外网地址"（内网穿透的域名）。没填也能用，只是绑定时要等一会儿。')),
        h('div.wx-kv', {}, h('span', {}, 'Token'), h('code', {}, data.token), copy(data.token)),
        h('small.quiet', {}, '点提交时微信会来这里验一次，验过了才保存成功。')),
      h('li', {}, '页面上"模板消息接口"点新增测试模板：标题写"研发工作台通知"，内容复制下面三行，提交后把"模板ID"填到下面。',
        h('div.wx-template', {}, h('pre', {}, data.template_text), copy(data.template_text)))),
    h('div.wx-form', {}, h('label', {}, 'appID', appid), h('label', {}, 'appsecret', secret), h('label', {}, '模板 ID', template)),
    h('div.row', {},
      h('button.btn.primary.small', {type: 'button', onclick: async () => {
        await api('/wechat/setup', 'PUT', {appid: appid.value, secret: secret.value, template: template.value});
        try { const out = await api('/wechat/check', 'POST'); status.textContent = `连上了，测试号现在有 ${out.followers} 人关注。`; status.className = 'small good-ink'; done(); }
        catch (error) { status.textContent = error.message; status.className = 'small bad-ink'; }
      }}, '存，并检查连不连得上'), status));
}

export async function wechatBox(host, {self = true} = {}) {
  if (!host) return;
  let data = await api('/wechat/me');
  let timer = null;
  const head = (tag) => h('header.block-head', {}, h('h3', {}, '微信推送'), tag);
  const draw = () => {
    clearTimeout(timer);
    if (!data.ready) {
      host.replaceChildren(head(h('span.st-tag', {}, '还没开通')), data.admin ? setupBox(data, async () => { data = await api('/wechat/me'); draw(); }) : h('p.quiet', {}, '管理员还没开通微信推送。'));
      return;
    }
    const change = data.admin ? h('details.wx-token', {}, h('summary', {}, '测试号设置'), setupBox(data, async () => { data = await api('/wechat/me'); draw(); })) : null;
    if (data.bound) {
      host.replaceChildren(head(h('span.st-tag.done', {}, '已绑定')),
        h('p.quiet', {}, self ? '要你拍板的事、你的对话的通知会推到你的微信（你正在用工作台时不推）。' : ''),
        h('div.row', {}, h('button.btn.small', {type: 'button', onclick: async () => { const out = await api('/wechat/test', 'POST'); toast(out.note); }}, '推一条试试'),
          h('button.btn.ghost.small', {type: 'button', onclick: async () => { await api('/wechat/unbind', 'POST'); data.bound = false; draw(); }}, '解绑')), change);
      return;
    }
    host.replaceChildren(head(h('span.st-tag', {}, '没绑定')),
      h('p.quiet', {}, '点一下出一个二维码，用微信扫一扫、关注"研发工作台"的测试号，就绑好了。'),
      h('button.btn.primary.small', {type: 'button', onclick: start}, icon('plus', 13), '扫码绑定我的微信'), change);
  };
  const start = async () => {
    const code = await api('/wechat/bind', 'POST');
    const lib = await qrLib();
    const qr = lib(0, 'M'); qr.addData(code.qr); qr.make();
    const note = h('p.quiet.small', {}, '用微信扫一扫（30 分钟内有效）。扫完关注了，这里会自己变成"已绑定"，微信里也会收到一句"绑定好了"。');
    host.replaceChildren(head(h('span.st-tag.open', {}, '等你扫码')), h('div.wx-qr', {html: qr.createImgTag(5, 8)}), note,
      h('button.btn.ghost.small', {type: 'button', onclick: draw}, '不绑了'));
    const began = Date.now();
    const ask = async () => {
      if (!host.isConnected || Date.now() - began > 30 * 60e3) return;
      try {
        const out = await api('/wechat/poll?code=' + encodeURIComponent(code.code));
        if (out.bound) { data.bound = true; toast('微信绑好了'); draw(); return; }
      } catch (error) { note.textContent = error.message; return; }
      timer = setTimeout(ask, 3000);
    };
    timer = setTimeout(ask, 3000);
  };
  draw();
}
