"""企业微信「智能机器人」长连接适配器（官方 API 模式,合规）。

协议(官方文档 developer.work.weixin.qq.com/document/path/101463):
- WebSocket 连 wss://openws.work.weixin.qq.com,`aibot_subscribe`(bot_id+secret) 鉴权;
- 每 ~30s 发 `{"cmd":"ping"}` 心跳;服务端长时间无心跳会断开;
- 用户单聊/群聊@机器人 → `aibot_msg_callback` 推送(免加解密,直接 JSON);
- 回复:`aibot_respond_msg` + msgtype=stream(stream.id 唯一,finish=true 一次成型),
  必须透传回调里的 req_id;24h 窗口,单会话 30 条/分钟;
- 新连接建立会踢掉旧连接(disconnected_event),同一机器人同时只允许一条长连接。

WebSocket 客户端为纯标准库自实现(RFC 6455 客户端侧:HTTP Upgrade 握手 + 帧编解码 +
客户端掩码,TLS 走 ssl 模块)——与本项目 wecom_crypto 自实现 AES 同一传统,零第三方依赖。

    ADAPTER=aibot WECOM_AIBOT_ID=xxx WECOM_AIBOT_SECRET=yyy python3 main.py

适用边界:智能机器人服务**企业内部成员**(单聊+群聊@bot);对外部微信客户请用
ADAPTER=kf(微信客服,需企业认证+公网回调)。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
import uuid
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.adapter import Adapter
from core.message import Message

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 固定 GUID
_DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"
_PING_INTERVAL = 30.0   # 官方建议 30s 心跳
_RECONNECT_MAX = 30.0   # 断线重连指数退避上限
_MAX_FRAME = 4 * 1024 * 1024  # 单帧上限,防异常长度攻击/失配

# 帧 opcode
_OP_CONT, _OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING, _OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WecomAibotError(RuntimeError):
    """智能机器人长连接协议错误。"""


# ── RFC 6455 帧编解码(模块级纯函数,便于离线单测) ──────────────

def _encode_frame(opcode: int, payload: bytes) -> bytes:
    """客户端→服务端帧:FIN=1,必须带 4 字节掩码(RFC 6455 §5.3)。"""
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n <= 0xFFFF:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


def _read_frame(read_exact) -> tuple[int, bool, bytes]:
    """读一帧,返回 (opcode, fin, payload)。read_exact(n)->bytes 由调用方注入(可测)。"""
    b0, b1 = read_exact(2)
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)  # 服务端帧不应带掩码,但按协议兼容处理
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read_exact(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read_exact(8))[0]
    if n > _MAX_FRAME:
        raise WecomAibotError(f"帧过大: {n} 字节")
    mask = read_exact(4) if masked else b""
    payload = read_exact(n) if n else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, fin, payload


class _WSClient:
    """最小 WebSocket 客户端(纯 stdlib):握手/收发文本帧/自动回 pong/close。"""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "wss":
            raise WecomAibotError(f"仅支持 wss://,得到 {url!r}")
        host = parsed.hostname or ""
        port = parsed.port or 443
        path = parsed.path or "/"
        raw = socket.create_connection((host, port), timeout=timeout)
        ctx = ssl.create_default_context()
        self._sock = ctx.wrap_socket(raw, server_hostname=host)
        self._sock.settimeout(timeout)
        self._wlock = threading.Lock()  # api_server 线程可能并发调 send
        self._handshake(host, path)

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(req.encode())
        # 逐字节读到头部结束(响应体不属于 HTTP 层)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(1)
            if not chunk:
                raise WecomAibotError("握手中断:服务端关闭连接")
            buf += chunk
            if len(buf) > 16384:
                raise WecomAibotError("握手响应头过大")
        status_line = buf.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in f" {status_line} ":
            raise WecomAibotError(f"握手失败: {status_line}")
        want = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        accept = ""
        for line in buf.decode("latin-1").split("\r\n")[1:]:
            k, _, v = line.partition(":")
            if k.strip().lower() == "sec-websocket-accept":
                accept = v.strip()
        if accept != want:
            raise WecomAibotError("握手失败: Sec-WebSocket-Accept 校验不过")

    def _read_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self._sock.recv(n - len(out))
            if not chunk:
                raise WecomAibotError("连接被服务端关闭")
            out += chunk
        return out

    def send_text(self, text: str) -> None:
        with self._wlock:
            self._sock.sendall(_encode_frame(_OP_TEXT, text.encode("utf-8")))

    def recv_text(self) -> str | None:
        """阻塞收一条完整文本消息;协议层 ping 自动回 pong;close 返回 None。

        socket 超时(settimeout)会抛 socket.timeout,由调用方用于心跳节拍。
        """
        assembled = b""
        while True:
            opcode, fin, payload = _read_frame(self._read_exact)
            if opcode == _OP_PING:
                with self._wlock:
                    self._sock.sendall(_encode_frame(_OP_PONG, payload))
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                return None
            if opcode in (_OP_TEXT, _OP_BIN, _OP_CONT):
                assembled += payload
                if fin:
                    return assembled.decode("utf-8", errors="replace")

    def settimeout(self, t: float) -> None:
        self._sock.settimeout(t)

    def close(self) -> None:
        try:
            with self._wlock:
                self._sock.sendall(_encode_frame(_OP_CLOSE, b""))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ── 适配器 ─────────────────────────────────────────────────────

class WecomAibotAdapter(Adapter):
    """智能机器人长连接适配器:订阅→收回调→映射 Message→回复。"""

    def __init__(self, ws_factory=None) -> None:
        self.bot_id = os.environ.get("WECOM_AIBOT_ID", "").strip()
        self.secret = os.environ.get("WECOM_AIBOT_SECRET", "").strip()
        self.ws_url = os.environ.get("WECOM_AIBOT_WS_URL", _DEFAULT_WS_URL)
        if not (self.bot_id and self.secret):
            raise RuntimeError("ADAPTER=aibot 需要 WECOM_AIBOT_ID 与 WECOM_AIBOT_SECRET(.env)")
        self._ws_factory = ws_factory or (lambda: _WSClient(self.ws_url))
        self._ws: "_WSClient | None" = None
        self._running = False
        # 被动回复需要透传回调 req_id:记每会话最近一次回调的 (req_id, 时间戳)
        self._reply_ctx: dict[str, tuple[str, float]] = {}
        self._ctx_lock = threading.Lock()

    # -- 协议帧 --
    @staticmethod
    def _req_id() -> str:
        return uuid.uuid4().hex

    def _send_cmd(self, cmd: str, body: dict | None = None, req_id: str | None = None) -> None:
        frame = {"cmd": cmd, "headers": {"req_id": req_id or self._req_id()}}
        if body is not None:
            frame["body"] = body
        assert self._ws is not None
        self._ws.send_text(json.dumps(frame, ensure_ascii=False))

    def _subscribe(self) -> None:
        self._send_cmd("aibot_subscribe", {"bot_id": self.bot_id, "secret": self.secret})
        # 等订阅响应至多 15s:socket 超时(构造时 10s)在环内接住重试,不穿透到重连逻辑
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                text = self._ws.recv_text()
            except socket.timeout:
                continue
            if text is None:
                raise WecomAibotError("订阅期间连接被关闭")
            data = json.loads(text)
            if "errcode" in data:
                if data["errcode"] != 0:
                    raise WecomAibotError(f"订阅失败 errcode={data['errcode']} errmsg={data.get('errmsg')}")
                print("[aibot] 订阅成功,长连接就绪", file=sys.stderr)
                return
        raise WecomAibotError("订阅超时:15s 未收到响应")

    # -- 消息映射 --
    def _to_message(self, body: dict) -> Message | None:
        if body.get("msgtype") != "text":
            return None  # MVP 只接文本;图片/语音等后续按需扩展
        userid = (body.get("from") or {}).get("userid") or "unknown"
        chattype = body.get("chattype") or "single"
        # 单聊回调无 chatid:以发送者为会话键,保证同一用户同一会话
        chat_id = body.get("chatid") or f"aibot-single-{userid}"
        content = (body.get("text") or {}).get("content") or ""
        # 群聊里内容形如 "@咕咕嘎嘎 问题正文":剥掉开头的 @机器人 提及。
        # 企微 @提及 后常跟 U+2005(四分之一空铛)而非普通空格,一并处理;无分隔符时保留原文。
        if content.startswith("@"):
            for sep in (" ", " ", "　"):
                _, s, rest = content.partition(sep)
                if s and rest:
                    content = rest
                    break
        return Message(
            chat_id=chat_id, chat_type="group" if chattype == "group" else "single",
            msg_id=body.get("msgid") or self._req_id(), sender_id=userid,
            sender_name=userid, content=content.strip(),
            is_at_bot=(chattype == "group"),  # 群聊回调仅在 @机器人 时触达
            timestamp=int(time.time() * 1000), raw=body)

    # -- Adapter 契约 --
    def start(self, on_message) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                self._ws = self._ws_factory()
                self._subscribe()
                backoff = 1.0
                self._recv_loop(on_message)
            except (WecomAibotError, OSError, json.JSONDecodeError, ssl.SSLError) as exc:
                print(f"[aibot] 连接异常({exc.__class__.__name__}: {exc}),{backoff:.0f}s 后重连",
                      file=sys.stderr)
            finally:
                if self._ws is not None:
                    self._ws.close()
                    self._ws = None
            if not self._running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX)

    def _recv_loop(self, on_message) -> None:
        assert self._ws is not None
        self._ws.settimeout(5.0)  # 短超时轮询,兼做心跳节拍
        last_ping = time.time()
        while self._running:
            if time.time() - last_ping >= _PING_INTERVAL:
                self._send_cmd("ping")
                last_ping = time.time()
            try:
                text = self._ws.recv_text()
            except socket.timeout:
                continue
            if text is None:
                raise WecomAibotError("服务端关闭连接")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print("[aibot] 收到非 JSON 帧,忽略", file=sys.stderr)
                continue
            self._dispatch(data, on_message)

    def _dispatch(self, data: dict, on_message) -> None:
        cmd = data.get("cmd") or ""
        req_id = (data.get("headers") or {}).get("req_id") or ""
        body = data.get("body") or {}
        if cmd == "aibot_msg_callback":
            msg = self._to_message(body)
            if msg is None:
                return
            with self._ctx_lock:
                self._reply_ctx[msg.chat_id] = (req_id, time.time())
                if len(self._reply_ctx) > 1024:  # 上下文只增会漏:顺手剔除超 24h 窗口的过期项
                    cutoff = time.time() - 24 * 3600
                    for k in [k for k, (_, ts) in self._reply_ctx.items() if ts < cutoff]:
                        del self._reply_ctx[k]
            try:
                on_message(msg)
            except Exception as exc:  # noqa: BLE001  # 业务异常不拖垮长连接
                print(f"[aibot] on_message 异常: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        elif cmd == "aibot_event_callback":
            etype = (body.get("event") or {}).get("eventtype")
            if etype == "disconnected_event":
                # 新连接踢旧连接:本连接即将失效,交给重连循环
                raise WecomAibotError("被新连接踢下线(disconnected_event)")
            print(f"[aibot] 事件回调: {etype}(暂不处理)", file=sys.stderr)
        # errcode 响应帧(ping/回复的 ack)静默即可

    def send(self, chat_id: str, text: str) -> None:
        """回复消息:透传该会话最近一次回调的 req_id,流式一次成型(finish=true)。"""
        if self._ws is None:
            raise WecomAibotError("长连接未就绪")
        with self._ctx_lock:
            ctx = self._reply_ctx.get(chat_id)
            if ctx and time.time() - ctx[1] > 24 * 3600:  # 官方 24h 回复窗口,过期即失效
                del self._reply_ctx[chat_id]
                ctx = None
        if not ctx:
            # 24h 窗口内无回调上下文(如纯人工主动发起):MVP 暂不支持主动推送
            print(f"[aibot] 会话 {chat_id} 无回复上下文(需先有用户消息),本条未发送", file=sys.stderr)
            return
        self._send_cmd("aibot_respond_msg",
                       {"msgtype": "stream",
                        "stream": {"id": self._req_id(), "finish": True, "content": text}},
                       req_id=ctx[0])

    def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            self._ws.close()
