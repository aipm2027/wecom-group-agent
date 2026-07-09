"""运营管理后台（零依赖单文件 Web 控制台）。

设计：与 api_server 相同的可测分层 —— ConsoleApp.handle() 纯逻辑（fetcher 可注入，离线单测
不起 socket），HTTP 绑定只是薄壳。浏览器只与本进程同源通信；本进程把 /api/* 反向代理到
api_server 并在服务端注入 ADMIN_TOKEN —— **token 绝不下发到前端**。

访问控制：CONSOLE_PASSWORD（缺省回落 ADMIN_TOKEN）登录换 HttpOnly Cookie；两者都未配置
= 开发模式放行（启动时告警）。代理的写操作要求 X-Requested-With 头（防跨站表单 CSRF）。

    ADMIN_TOKEN=xxx API_BASE=http://127.0.0.1:8080 CONSOLE_PORT=8090 python3 admin_console.py
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_MAX_BODY = 1024 * 1024  # 与 api_server 对齐：请求体上限 1MB
_COOKIE = "console_session"
_PROXY_TIMEOUT = 30  # 秒；preview 会等 LLM，放宽


def _default_fetcher(method: str, url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    """真实转发到 api_server；网络错误统一 502，不让控制台崩。"""
    req = urllib.request.Request(url, data=body if body else None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也带响应体，原样透传
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        detail = json.dumps({"error": "api unreachable", "detail": str(exc)[:200]}, ensure_ascii=False)
        return 502, detail.encode("utf-8")


class ConsoleApp:
    """纯逻辑层：返回 (status, content_type, body_bytes, extra_headers)。"""

    def __init__(self, api_base: str = "http://127.0.0.1:8080", admin_token: str = "",
                 password: str = "", fetcher=None) -> None:
        self.api_base = api_base.rstrip("/")
        self.admin_token = admin_token
        self.password = password or admin_token  # 缺省复用 ADMIN_TOKEN 当登录口令
        self.fetcher = fetcher or _default_fetcher
        self._sessions: set[str] = set()  # 已登录会话 cookie（随机 32 hex，进程内有效）

    # --- 会话/鉴权 ---
    def _cookie_of(self, headers: dict) -> str:
        raw = headers.get("cookie") or headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == _COOKIE:
                return v
        return ""

    def _authed(self, headers: dict) -> bool:
        if not self.password:
            return True  # 开发模式（启动时已告警）
        return self._cookie_of(headers) in self._sessions

    # --- 主入口 ---
    def handle(self, method: str, path: str, body: bytes, headers: dict):
        if path == "/login" and method == "POST":
            return self._login(body)
        if path == "/logout" and method == "POST":
            self._sessions.discard(self._cookie_of(headers))
            return 200, "application/json", b'{"ok": true}', {"Set-Cookie": f"{_COOKIE}=; Max-Age=0; Path=/"}
        if path.startswith("/api/"):
            return self._proxy(method, path, body, headers)
        if path == "/" and method == "GET":
            page = CONSOLE_HTML if self._authed(headers) else LOGIN_HTML
            return 200, "text/html; charset=utf-8", page.encode("utf-8"), {}
        return 404, "application/json", b'{"error": "not found"}', {}

    def _login(self, body: bytes):
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        given = str(data.get("password") or "")
        if not self.password:
            return 200, "application/json", b'{"ok": true}', {}  # 开发模式无需登录
        if not hmac.compare_digest(given, self.password):
            return 401, "application/json", b'{"error": "\\u53e3\\u4ee4\\u9519\\u8bef"}', {}
        token = secrets.token_hex(16)
        self._sessions.add(token)
        cookie = f"{_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/"
        return 200, "application/json", b'{"ok": true}', {"Set-Cookie": cookie}

    def _proxy(self, method: str, path: str, body: bytes, headers: dict):
        if not self._authed(headers):
            return 401, "application/json", b'{"error": "unauthorized"}', {}
        # 防跨站表单 CSRF：写操作必须带 JS 才能设置的自定义头（同源 fetch 会带上）
        if method != "GET" and (headers.get("x-requested-with") or "") != "fetch":
            return 403, "application/json", b'{"error": "missing X-Requested-With"}', {}
        out_headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.admin_token:
            out_headers["X-Admin-Token"] = self.admin_token  # 服务端注入，前端永远拿不到
        status, payload = self.fetcher(method, self.api_base + path, body, out_headers)
        return status, "application/json; charset=utf-8", payload, {}


def _make_handler(app: ConsoleApp):
    class Handler(BaseHTTPRequestHandler):
        def _run(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > _MAX_BODY:
                self._respond(413, "application/json", b'{"error": "payload too large"}', {})
                return
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, ctype, data, extra = app.handle(method, self.path.split("?")[0], body, headers)
            self._respond(status, ctype, data, extra)

        def _respond(self, status: int, ctype: str, data: bytes, extra: dict) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._run("GET")

        def do_POST(self) -> None:
            self._run("POST")

        def log_message(self, *args) -> None:  # 静音默认访问日志
            pass

    return Handler


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 客服工作台</title><style>
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#fff;padding:36px 32px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.08);width:320px}
h1{font-size:18px;margin:0 0 20px;color:#111}
input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d9d9d9;border-radius:6px;font-size:14px;margin-bottom:12px}
button{width:100%;padding:10px;background:#07c160;color:#fff;border:none;border-radius:6px;font-size:15px;cursor:pointer}
button:hover{background:#06ad56}.err{color:#e64340;font-size:13px;min-height:18px;margin-bottom:6px}
</style></head><body>
<div class="card"><h1>🛍️ 私域客服工作台</h1>
<div class="err" id="err"></div>
<input type="password" id="pw" placeholder="访问口令（CONSOLE_PASSWORD）" onkeydown="if(event.key==='Enter')go()">
<button onclick="go()">登录</button></div>
<script>
async function go(){
  const r = await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'fetch'},
    body:JSON.stringify({password:document.getElementById('pw').value})});
  if(r.ok){location.reload()}else{document.getElementById('err').textContent='口令错误，请重试'}
}
</script></body></html>"""


CONSOLE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>客服工作台</title><style>
*{box-sizing:border-box}body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f0f2f5;height:100vh;display:flex;flex-direction:column}
header{background:#fff;border-bottom:1px solid #e5e5e5;padding:10px 16px;display:flex;align-items:center;gap:16px}
header h1{font-size:16px;margin:0}#metrics{font-size:12px;color:#666;flex:1}
header button{padding:5px 12px;border:1px solid #d9d9d9;background:#fff;border-radius:5px;cursor:pointer;font-size:13px}
main{flex:1;display:flex;min-height:0}
#left{width:300px;background:#fff;border-right:1px solid #e5e5e5;display:flex;flex-direction:column}
#tabs{display:flex;border-bottom:1px solid #eee}#tabs div{flex:1;text-align:center;padding:9px 0;font-size:13px;cursor:pointer;color:#666}
#tabs div.on{color:#07c160;border-bottom:2px solid #07c160;font-weight:600}
#list{flex:1;overflow-y:auto}
.conv{padding:10px 14px;border-bottom:1px solid #f5f5f5;cursor:pointer}
.conv:hover{background:#f7f7f7}.conv.on{background:#e8f6ee}
.conv .id{font-size:14px;font-weight:600;color:#222;display:flex;align-items:center;gap:6px}
.conv .last{font-size:12px;color:#999;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}
.tag{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:normal}
.tag.need{background:#fff1f0;color:#e64340}.tag.human{background:#e6f4ff;color:#1677ff}
#right{flex:1;display:flex;flex-direction:column;min-width:0}
#convbar{background:#fff;border-bottom:1px solid #e5e5e5;padding:9px 16px;display:flex;align-items:center;gap:10px}
#convbar .title{font-weight:600;flex:1;font-size:14px}#convbar .reason{color:#e64340;font-size:12px}
#convbar button{padding:5px 14px;border-radius:5px;border:1px solid #d9d9d9;background:#fff;cursor:pointer;font-size:13px}
#convbar button.primary{background:#07c160;border-color:#07c160;color:#fff}
#msgs{flex:1;overflow-y:auto;padding:16px}
.msg{display:flex;margin-bottom:12px}.msg.mine{justify-content:flex-end}
.bubble{max-width:62%;padding:9px 13px;border-radius:8px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg .bubble{background:#fff}.msg.bot .bubble{background:#dcf8c6}.msg.human .bubble{background:#cfe8ff}
.meta{font-size:11px;color:#aaa;margin:0 8px 3px;align-self:flex-end}
#inputbar{background:#fff;border-top:1px solid #e5e5e5;padding:10px 16px;display:flex;gap:10px}
#inputbar textarea{flex:1;resize:none;height:56px;border:1px solid #d9d9d9;border-radius:6px;padding:8px 10px;font-size:14px;font-family:inherit}
#inputbar button{padding:0 22px;background:#07c160;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px}
#inputbar button:disabled{background:#a7e3c3;cursor:not-allowed}
#empty{flex:1;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:14px}
#pvmask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);align-items:center;justify-content:center}
#pv{background:#fff;border-radius:10px;width:520px;max-width:92vw;padding:20px}
#pv h2{font-size:15px;margin:0 0 12px}#pv textarea{width:100%;height:64px;border:1px solid #d9d9d9;border-radius:6px;padding:8px;font-size:14px;font-family:inherit}
#pvout{margin-top:12px;background:#f7f7f7;border-radius:6px;padding:12px;font-size:13px;min-height:44px;white-space:pre-wrap}
#pv .row{display:flex;gap:10px;margin-top:12px;justify-content:flex-end}
#pv button{padding:7px 18px;border-radius:6px;border:1px solid #d9d9d9;background:#fff;cursor:pointer;font-size:13px}
#pv button.primary{background:#07c160;border-color:#07c160;color:#fff}
.toast{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:8px 18px;border-radius:6px;font-size:13px;opacity:.94;z-index:99}
</style></head><body>
<header><h1>🛍️ 私域客服工作台</h1><span id="metrics">加载中…</span>
<button onclick="openPreview()">🧪 试聊调试</button><button onclick="logout()">退出</button></header>
<main>
<div id="left"><div id="tabs"><div class="on" data-t="all" onclick="setTab('all')">全部会话</div>
<div data-t="queue" onclick="setTab('queue')">待人工 <span id="qn"></span></div></div>
<div id="list"></div></div>
<div id="right"><div id="empty">← 选择一个会话</div></div>
</main>
<div id="pvmask"><div id="pv"><h2>🧪 试聊调试（不入库、不下发，仅测 agent 回复）</h2>
<textarea id="pvtext" placeholder="模拟客户消息，如：这款怎么卖？"></textarea>
<div id="pvout">回复将显示在这里</div>
<div class="row"><button onclick="closePreview()">关闭</button><button class="primary" id="pvbtn" onclick="runPreview()">发送测试</button></div>
</div></div>
<script>
let tab='all', active=null, convs=[], timer=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(method,path,body){
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Requested-With':'fetch'},
    body:body?JSON.stringify(body):undefined});
  if(r.status===401){location.reload();return null}
  return r.json().catch(()=>({}));
}
function toast(t){const d=document.createElement('div');d.className='toast';d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.remove(),2200)}
async function loadMetrics(){const m=await api('GET','/api/metrics');if(!m)return;
  $('metrics').textContent=`会话 ${m.conversations??0} · 消息 ${m.messages??0} · 待人工 ${m.needs_human??0} · 已接管 ${m.human_controlled??0}`;
  $('qn').textContent=m.needs_human?`(${m.needs_human})`:'';}
async function loadList(){
  const d=await api('GET',tab==='queue'?'/api/queue':'/api/conversations');if(!d)return;
  convs=d.conversations||d.queue||[];
  $('list').innerHTML=convs.map(c=>`<div class="conv${c.chat_id===active?' on':''}" onclick="pick('${esc(c.chat_id)}')">
    <div class="id">${esc(c.chat_id)}
      ${c.needs_human?'<span class="tag need">待人工</span>':''}
      ${c.human_controlled?'<span class="tag human">已接管</span>':''}</div>
    <div class="last">${esc(c.last_message)||'（无消息）'}</div></div>`).join('')||'<div style="padding:20px;color:#bbb;font-size:13px;text-align:center">暂无会话</div>';
}
function setTab(t){tab=t;document.querySelectorAll('#tabs div').forEach(d=>d.classList.toggle('on',d.dataset.t===t));loadList()}
function pick(id){active=id;renderRight();loadMsgs();loadList()}
function renderRight(){
  const c=convs.find(x=>x.chat_id===active)||{};
  $('right').innerHTML=`<div id="convbar"><span class="title">${esc(active)}</span>
    <span class="reason">${esc(c.escalation_reason||'')}</span>
    ${c.human_controlled
      ?'<button onclick="release()">交回 AI</button>'
      :'<button class="primary" onclick="takeover()">人工接管</button>'}
  </div><div id="msgs"></div>
  <div id="inputbar"><textarea id="text" placeholder="以人工客服身份回复客户…（Ctrl/⌘+Enter 发送）"
    onkeydown="if((event.ctrlKey||event.metaKey)&&event.key==='Enter')send()"></textarea>
  <button id="sendbtn" onclick="send()">发送</button></div>`;
}
async function loadMsgs(){
  if(!active)return;
  const d=await api('GET',`/api/conversations/${encodeURIComponent(active)}/messages`);if(!d)return;
  const box=$('msgs');if(!box)return;
  const stick=box.scrollHeight-box.scrollTop-box.clientHeight<60;
  box.innerHTML=(d.messages||[]).map(m=>{
    const cls=m.is_bot?'mine bot':(m.is_human_agent?'mine human':'');
    const who=m.is_bot?'AI':(m.is_human_agent?(m.sender_name||'人工'):(m.sender_name||m.sender_id));
    const t=m.timestamp>1e12?new Date(m.timestamp).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'';
    return m.is_bot||m.is_human_agent
      ?`<div class="msg ${cls}"><span class="meta">${esc(who)} ${t}</span><div class="bubble">${esc(m.content)}</div></div>`
      :`<div class="msg"><div class="bubble">${esc(m.content)}</div><span class="meta">${esc(who)} ${t}</span></div>`;
  }).join('');
  if(stick)box.scrollTop=box.scrollHeight;
}
async function send(){
  const el=$('text');const text=(el.value||'').trim();if(!text)return;
  $('sendbtn').disabled=true;
  const r=await api('POST',`/api/conversations/${encodeURIComponent(active)}/messages`,{text});
  $('sendbtn').disabled=false;
  if(r&&r.ok){el.value='';toast(r.sent?'已发送给客户':'已入库（渠道未连接，未真实下发）');loadMsgs()}
  else toast('发送失败：'+((r&&r.error)||'未知错误'));
}
async function takeover(){const r=await api('POST',`/api/conversations/${encodeURIComponent(active)}/takeover`);
  if(r&&r.ok){toast('已接管，AI 已静默');await loadList();renderRight();loadMsgs()}}
async function release(){const r=await api('POST',`/api/conversations/${encodeURIComponent(active)}/release`);
  if(r&&r.ok){toast('已交回 AI');await loadList();renderRight();loadMsgs()}}
function openPreview(){$('pvmask').style.display='flex'}
function closePreview(){$('pvmask').style.display='none'}
async function runPreview(){
  const text=($('pvtext').value||'').trim();if(!text)return;
  $('pvbtn').disabled=true;$('pvout').textContent='AI 思考中…';
  const r=await api('POST','/api/agent/preview',{text});
  $('pvbtn').disabled=false;
  $('pvout').textContent=r?((r.reply??'（agent 选择不回复）')+(r.needs_human?'\\n\\n⚠️ 此消息会触发转人工':'')) :'请求失败';
}
async function logout(){await api('POST','/logout');location.reload()}
loadMetrics();loadList();
timer=setInterval(()=>{loadMetrics();loadList();loadMsgs()},4000);
</script></body></html>"""


def main() -> None:
    import signal
    # 复用 main.py 的 .env 加载，配置口径一致
    from main import load_env_file
    load_env_file()
    api_base = os.environ.get("API_BASE", "http://127.0.0.1:8080")
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    password = os.environ.get("CONSOLE_PASSWORD", "")
    if not (password or admin_token):
        print("[console] 警告：未设置 CONSOLE_PASSWORD/ADMIN_TOKEN，控制台无口令放行（仅限本地开发）",
              file=sys.stderr)
    app = ConsoleApp(api_base=api_base, admin_token=admin_token, password=password)
    host = os.environ.get("CONSOLE_HOST", "127.0.0.1")
    port = int(os.environ.get("CONSOLE_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), _make_handler(app))

    def _term(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass
    print(f"[console] 工作台 http://{host}:{port} → API {api_base}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[console] 收到终止信号，正在关闭...", file=sys.stderr)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
