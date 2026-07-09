"""微信客服(企业微信客服)适配器 —— 官方合规 1:1 通道。

这是官方合规的客服通道，不进群，只能与用户一对一对话。
与 ntwork 违规群 hook 的区别：
- ntwork 通过注入 Windows 企微客户端获取消息，违反《企业微信用户协议》，有封号风险；
- 微信客服适配器通过官方 API 与回调实现，无封号风险，但需公网可访问的回调地址。
- 不支持群聊，仅支持单聊。

参考 docs/技术文档/04-适配器与部署.md。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from core.adapter import Adapter, OnMessage
from core.message import Message
from adapters.wecom_crypto import WXBizMsgCrypt

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# access_token 失效/非法相关 errcode：命中则主动清缓存，下次强制重取
_AUTH_ERRCODES = {40001, 40014, 42001, 41001}
# 回调只处理客户发来的消息(origin==3)；4=系统事件、5=接待人员/机器人自己发送，必须跳过以防自问自答
_ORIGIN_CUSTOMER = 3
_MAX_CALLBACK_BODY = 1024 * 1024  # 回调请求体上限 1MB，超出直接拒绝，防 XML 炸弹/内存耗尽
_MAX_SYNC_PAGES = 20  # sync_msg 分页拉取的最大兜底页数，防异常 has_more 导致死循环


class WecomKfAdapter(Adapter):
    """企业微信客服(微信客服)适配器，官方合规 1:1 通道。"""

    def __init__(
        self,
        *,
        corp_id: str | None = None,
        kf_secret: str | None = None,
        callback_token: str | None = None,
        encoding_aes_key: str | None = None,
        callback_port: int | None = None,
        callback_path: str | None = None,
        cursor_path: str | None = None,
        token_path: str | None = None,
        http_get_json: Callable[[str], dict] | None = None,
        http_post_json: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self.corp_id = corp_id if corp_id is not None else os.environ.get("WECOM_CORP_ID", "")
        self.kf_secret = kf_secret if kf_secret is not None else os.environ.get("WECOM_KF_SECRET", "")
        self.callback_token = callback_token if callback_token is not None else os.environ.get("WECOM_CALLBACK_TOKEN", "")
        self.encoding_aes_key = encoding_aes_key if encoding_aes_key is not None else os.environ.get("WECOM_ENCODING_AES_KEY", "")
        self.callback_port = callback_port if callback_port is not None else int(os.environ.get("WECOM_CALLBACK_PORT", "9000"))
        self.callback_path = callback_path if callback_path is not None else os.environ.get("WECOM_CALLBACK_PATH", "/wecom/callback")
        self._http_get_json = http_get_json or self._default_http_get_json
        self._http_post_json = http_post_json or self._default_http_post_json
        if not self.encoding_aes_key:
            raise RuntimeError(
                "WecomKfAdapter 缺少 WECOM_ENCODING_AES_KEY（企业微信 43 位 EncodingAESKey）；"
                "请在 .env 配置后再用 ADAPTER=kf 启动。"
            )
        self._crypt = WXBizMsgCrypt(self.callback_token, self.encoding_aes_key, self.corp_id)
        self._access_token = ""
        self._token_expires = 0.0
        self._token_lock = threading.Lock()  # 保护 access_token 的 check-then-act，避免并发重复刷新
        # cursor 持久化：进程重启后从上次位置续拉，避免重复/漏消息（写失败不影响功能）
        self._cursor_path = (cursor_path if cursor_path is not None
                             else os.path.join(_ROOT, "data", "wecom_kf_cursor.txt"))
        self._cursor = self._load_cursor()
        # access_token 跨进程共享（可选）：配 token_path / WECOM_TOKEN_FILE 后，多副本经文件共享，
        # 减少各自 gettoken 造成的调用风暴；默认 None = 仅进程内内存缓存（单进程足够）。
        self._token_path = token_path or os.environ.get("WECOM_TOKEN_FILE") or None

    # ------------------------------------------------------------------
    # 默认 HTTP 客户端(零第三方依赖)
    # ------------------------------------------------------------------

    def _default_http_get_json(self, url: str) -> dict:
        try:
            req = Request(url, method="GET", headers={"User-Agent": "wecom-group-agent/1.0"})
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except Exception as exc:
            # 不打印 URL，避免泄漏 corpsecret 等敏感信息
            print(f"[WecomKfAdapter] GET 请求失败: {exc.__class__.__name__}", file=sys.stderr)
            return {}

    def _default_http_post_json(self, url: str, payload: dict) -> dict:
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "wecom-group-agent/1.0",
                },
            )
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except Exception as exc:
            print(f"[WecomKfAdapter] POST 请求失败: {exc.__class__.__name__}", file=sys.stderr)
            return {}

    # ------------------------------------------------------------------
    # cursor 持久化
    # ------------------------------------------------------------------

    def _load_cursor(self) -> str:
        try:
            with open(self._cursor_path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _persist_cursor(self, cursor: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._cursor_path), exist_ok=True)
            with open(self._cursor_path, "w", encoding="utf-8") as f:
                f.write(cursor)
        except OSError as exc:
            print(f"[WecomKfAdapter] cursor 持久化失败(已忽略): {exc.__class__.__name__}", file=sys.stderr)

    def _load_token_file(self) -> tuple[str, float]:
        if not self._token_path:
            return "", 0.0
        try:
            with open(self._token_path, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("access_token", ""), float(d.get("expires", 0))
        except (OSError, ValueError):
            return "", 0.0

    def _save_token_file(self, token: str, expires: float) -> None:
        if not self._token_path:
            return
        try:
            os.makedirs(os.path.dirname(self._token_path) or ".", exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                json.dump({"access_token": token, "expires": expires}, f)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 企业微信 API 内部方法
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        # 双检锁：并发时只让一个线程真正刷新 token，其余复用结果
        with self._token_lock:
            now = time.time()
            if self._access_token and now < self._token_expires - 60:
                return self._access_token
            # 跨进程共享：先看文件里是否有其他副本刚刷新的有效 token
            if self._token_path:
                tok, exp = self._load_token_file()
                if tok and now < exp - 60:
                    self._access_token, self._token_expires = tok, exp
                    return tok
            url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corp_id}&corpsecret={self.kf_secret}"
            try:
                data = self._http_get_json(url)
            except Exception as exc:
                print(f"[WecomKfAdapter] 获取 access_token 失败: {exc.__class__.__name__}", file=sys.stderr)
                return ""
            if not isinstance(data, dict) or "access_token" not in data:
                print(f"[WecomKfAdapter] 获取 access_token 失败 errcode="
                      f"{data.get('errcode') if isinstance(data, dict) else '?'}", file=sys.stderr)
                return ""
            self._access_token = data["access_token"]
            self._token_expires = now + data.get("expires_in", 7200)
            self._save_token_file(self._access_token, self._token_expires)
            return self._access_token

    def _invalidate_token_if_needed(self, data: dict) -> None:
        """API 返回 token 失效类 errcode 时清空缓存，下次强制重取(否则 2 小时内一直用坏 token)。"""
        if isinstance(data, dict) and data.get("errcode") in _AUTH_ERRCODES:
            with self._token_lock:
                self._access_token = ""
                self._token_expires = 0.0
                self._save_token_file("", 0.0)  # 通知其他副本该 token 已失效

    def _sync_msg(self, token: str, cursor: str) -> list[dict]:
        try:
            access_token = self._get_access_token()
            if not access_token:
                return []
            url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={access_token}"
            all_msgs: list[dict] = []
            # 分页拉取：has_more=1 时用 next_cursor 继续；最多 20 页兜底防死循环
            for _ in range(_MAX_SYNC_PAGES):
                payload = {"token": token, "cursor": cursor, "limit": 1000}
                data = self._http_post_json(url, payload)
                errcode = data.get("errcode", 0)
                if errcode:
                    # errcode/errmsg 非密钥，可安全记录（便于定位 token 失效/频率限制等），
                    # 出错时不把错误响应当成“无消息”，避免静默丢消息
                    print(f"[WecomKfAdapter] sync_msg 返回错误 errcode={errcode} "
                          f"errmsg={data.get('errmsg', '')}", file=sys.stderr)
                    self._invalidate_token_if_needed(data)
                    break
                all_msgs.extend(data.get("msg_list", []))
                next_cursor = data.get("next_cursor", "")
                if next_cursor:
                    cursor = next_cursor
                    self._cursor = next_cursor
                    self._persist_cursor(next_cursor)
                if not data.get("has_more"):
                    break
            return all_msgs
        except Exception as exc:
            print(f"[WecomKfAdapter] sync_msg 失败: {exc.__class__.__name__}", file=sys.stderr)
            return []

    def _send_text(self, open_kfid: str, touser: str, content: str) -> dict:
        try:
            access_token = self._get_access_token()
            if not access_token:
                return {}
            url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}"
            payload = {
                "touser": touser,
                "open_kfid": open_kfid,
                "msgtype": "text",
                "text": {"content": content},
            }
            data = self._http_post_json(url, payload)
            self._invalidate_token_if_needed(data)
            return data
        except Exception as exc:
            print(f"[WecomKfAdapter] send_text 失败: {exc.__class__.__name__}", file=sys.stderr)
            return {}

    # ------------------------------------------------------------------
    # 回调处理(可独立调用,便于离线测试)
    # ------------------------------------------------------------------

    def _handle_post(self, body: str, msg_signature: str, timestamp: str, nonce: str, on_message: OnMessage) -> bool:
        """处理 POST 回调：解密事件 → 拉取消息 → 映射并投递。

        返回 True 表示处理成功；False 表示解密/参数/拉取失败（调用方据此返回非 200，让腾讯重试，
        避免消息永久丢失）。单条消息的 on_message 异常会被单独兜住，不影响整体成功判定。
        """
        try:
            event_xml = self._crypt.decrypt_msg(msg_signature, timestamp, nonce, body)
        except Exception as exc:
            print(f"[WecomKfAdapter] 解密失败: {exc.__class__.__name__}", file=sys.stderr)
            return False
        token = self._extract_xml_node(event_xml, "Token")
        open_kfid = self._extract_xml_node(event_xml, "OpenKfId")
        if not token or not open_kfid:
            print("[WecomKfAdapter] 事件 XML 缺少 Token 或 OpenKfId", file=sys.stderr)
            return False
        try:
            msg_list = self._sync_msg(token, self._cursor)
        except Exception as exc:
            print(f"[WecomKfAdapter] sync_msg 失败: {exc.__class__.__name__}", file=sys.stderr)
            return False
        for msg in msg_list:
            # 只处理客户发来的消息：跳过系统事件(4)与接待人员/机器人自己发的(5)，防自问自答死循环
            if msg.get("origin") not in (_ORIGIN_CUSTOMER, None):
                continue
            if msg.get("msgtype") != "text":
                continue
            text_content = msg.get("text", {}).get("content", "")
            external_userid = msg.get("external_userid", "")
            msg_open_kfid = msg.get("open_kfid", open_kfid)
            if not external_userid:
                continue
            message = Message(
                chat_type="single",
                is_at_bot=True,
                sender_id=external_userid,
                sender_name=external_userid,
                chat_id=f"{msg_open_kfid}:{external_userid}",
                content=text_content,
                msg_type="text",
                msg_id=msg.get("msgid", ""),
                timestamp=msg.get("send_time", 0),
                raw=msg,
            )
            try:
                on_message(message)
            except Exception as exc:
                print(f"[WecomKfAdapter] on_message 处理失败: {exc.__class__.__name__}", file=sys.stderr)
        return True

    @staticmethod
    def _extract_xml_node(xml_str: str, tag: str) -> str | None:
        try:
            root = ET.fromstring(xml_str)
            node = root.find(tag)
            return node.text if node is not None else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Adapter 接口
    # ------------------------------------------------------------------

    def send(self, chat_id: str, text: str) -> None:
        parts = chat_id.split(":", 1)
        if len(parts) != 2:
            print(f"[WecomKfAdapter] chat_id 格式非法: {chat_id}", file=sys.stderr)
            return
        open_kfid, touser = parts
        try:
            self._send_text(open_kfid, touser, text)
        except Exception as exc:
            print(f"[WecomKfAdapter] 发送消息失败: {exc.__class__.__name__}", file=sys.stderr)

    def start(self, on_message: OnMessage) -> None:
        """启动 HTTP 回调服务器，阻塞入口。

        注：使用单线程 TCPServer 顺序处理回调——Router 的去重/限流非线程安全，
        单线程可避免竞态；高并发需求应在前置反向代理层解决，而非在此改多线程。
        """

        class _CallbackHandler(BaseHTTPRequestHandler):
            def __init__(self, adapter: "WecomKfAdapter", on_message: OnMessage, *args, **kwargs) -> None:
                self._adapter = adapter
                self._on_message = on_message
                super().__init__(*args, **kwargs)

            def log_message(self, format: str, *args) -> None:
                # 抑制默认日志
                pass

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != self._adapter.callback_path:
                    self.send_error(404)
                    return
                params = parse_qs(parsed.query)
                msg_signature = params.get("msg_signature", [""])[0]
                timestamp = params.get("timestamp", [""])[0]
                nonce = params.get("nonce", [""])[0]
                echostr = params.get("echostr", [""])[0]
                try:
                    plaintext = self._adapter._crypt.verify_url(msg_signature, timestamp, nonce, echostr)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(plaintext.encode("utf-8"))
                except Exception:
                    self.send_response(403)
                    self.end_headers()

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != self._adapter.callback_path:
                    self.send_error(404)
                    return
                content_length = int(self.headers.get("Content-Length") or 0)
                if content_length > _MAX_CALLBACK_BODY:
                    self.send_response(413)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                msg_signature = params.get("msg_signature", [""])[0]
                timestamp = params.get("timestamp", [""])[0]
                nonce = params.get("nonce", [""])[0]
                body = self.rfile.read(content_length).decode("utf-8", "replace")
                try:
                    ok = self._adapter._handle_post(body, msg_signature, timestamp, nonce, self._on_message)
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    # 成功：回 success，腾讯不再重试
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"success")
                else:
                    # 处理失败：回 500 让腾讯重试，避免消息永久丢失（cursor 未推进，重试会重新拉取）
                    self.send_response(500)
                    self.end_headers()

        def handler_factory(*args, **kwargs):
            return _CallbackHandler(self, on_message, *args, **kwargs)
        with TCPServer(("", self.callback_port), handler_factory) as httpd:
            print(
                f"[WecomKfAdapter] 回调服务器监听 http://0.0.0.0:{self.callback_port}{self.callback_path}",
                flush=True,
            )
            httpd.serve_forever()
