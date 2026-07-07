"""REST API 层（运营后台/前端对接用）。

设计：业务逻辑放在无 socket 的 ApiApp.handle() 里（可纯离线单测），HTTP 绑定只是薄壳。
纯标准库 http.server。鉴权用 ADMIN_TOKEN（Bearer 或 X-Admin-Token 头）。

端点（契约详见 docs/技术文档/06-系统架构与API.md）：
  GET  /api/health                              健康检查（免鉴权）
  GET  /api/conversations                       会话列表
  GET  /api/conversations/{id}/messages         某会话消息
  POST /api/conversations/{id}/messages         人工发消息 {text, sender_name?}
  POST /api/conversations/{id}/takeover         人工接管（agent 静默）
  POST /api/conversations/{id}/release          交回 agent
  POST /api/agent/preview                       试聊 {text, chat_id?} → 不持久化
  GET  /api/config                              当前配置（不含密钥）
  GET  /api/metrics                             简单指标

    ADMIN_TOKEN=xxx API_PORT=8080 python3 api_server.py
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.message import Message
from core.session import Session

HUMAN_SENDER_ID = "human-agent"


class ApiApp:
    """纯逻辑层：无 socket，方便离线单测。"""

    def __init__(self, sessions, handler, adapter=None, admin_token: str = "") -> None:
        self.sessions = sessions
        self.handler = handler
        self.adapter = adapter
        self.admin_token = admin_token
        self._preview_seq = 0

    # --- 鉴权 ---
    def _authed(self, headers: dict) -> bool:
        if not self.admin_token:
            return True  # 未配置 token = 开发模式放行（启动时已告警）
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            if auth[7:].strip() == self.admin_token:
                return True
        token = headers.get("x-admin-token") or headers.get("X-Admin-Token") or ""
        return token == self.admin_token

    # --- 主入口：返回 (status, dict) ---
    def handle(self, method: str, path: str, body: bytes, headers: dict) -> tuple[int, dict]:
        path = urlparse(path).path
        if path == "/api/health":
            return 200, {"ok": True}
        if not self._authed(headers):
            return 401, {"error": "unauthorized"}
        try:
            parts = [p for p in path.split("/") if p]  # ["api","conversations","u1","messages"]
            if path == "/api/conversations" and method == "GET":
                return 200, {"conversations": [self._conv_summary(s) for s in self.sessions.all()]}
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "conversations":
                chat_id = unquote(parts[2])
                action = parts[3]
                if action == "messages" and method == "GET":
                    return 200, {"chat_id": chat_id,
                                 "messages": [self._msg_dict(m) for m in self.sessions.get(chat_id).history]}
                if action == "messages" and method == "POST":
                    return self._human_send(chat_id, self._json(body))
                if action == "takeover" and method == "POST":
                    self.sessions.get(chat_id).take_over()
                    return 200, {"ok": True, "chat_id": chat_id, "human_controlled": True}
                if action == "release" and method == "POST":
                    self.sessions.get(chat_id).release()
                    return 200, {"ok": True, "chat_id": chat_id, "human_controlled": False}
            if path == "/api/agent/preview" and method == "POST":
                return self._preview(self._json(body))
            if path == "/api/config" and method == "GET":
                return 200, self._config()
            if path == "/api/metrics" and method == "GET":
                return 200, self._metrics()
            return 404, {"error": "not found", "path": path}
        except ValueError as exc:
            return 400, {"error": f"bad request: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return 500, {"error": "internal error", "detail": str(exc)[:200]}

    # --- 业务 ---
    def _human_send(self, chat_id: str, data: dict) -> tuple[int, dict]:
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        session = self.sessions.get(chat_id)
        self._preview_seq += 1
        session.add(Message(
            chat_id=chat_id, chat_type=session.history[-1].chat_type if session.history else "single",
            msg_id=f"human-{chat_id}-{self._preview_seq}", sender_id=HUMAN_SENDER_ID,
            sender_name=(data.get("sender_name") or "人工客服"), content=text, msg_type="text"))
        sent = False
        if self.adapter is not None:
            try:
                self.adapter.send(chat_id, text)
                sent = True
            except Exception:  # noqa: BLE001
                sent = False
        return 200, {"ok": True, "chat_id": chat_id, "sent": sent}

    def _preview(self, data: dict) -> tuple[int, dict]:
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        # 用一次性会话，不持久化、不影响真实会话
        session = Session(chat_id=data.get("chat_id") or "__preview__")
        self._preview_seq += 1
        msg = Message(chat_id=session.chat_id, chat_type="single",
                      msg_id=f"preview-{self._preview_seq}", sender_id="preview",
                      sender_name=(data.get("sender_name") or "客户"), content=text)
        session.add(msg)
        reply = self.handler.reply(msg, session)
        return 200, {"reply": reply, "needs_human": session.needs_human}

    def _config(self) -> dict:
        # 只回非敏感配置，绝不返回 LLM_API_KEY 等密钥
        return {
            "handler": os.environ.get("HANDLER", "echo"),
            "adapter": os.environ.get("ADAPTER", "mock" if os.environ.get("MOCK") == "1" else "ntwork"),
            "llm_model": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            "persona_file": os.environ.get("LLM_PERSONA_FILE", "prompts/persona.md"),
            "knowledge_file": os.environ.get("LLM_KNOWLEDGE_FILE", "prompts/knowledge.md"),
        }

    def _metrics(self) -> dict:
        sessions = self.sessions.all()
        return {
            "conversations": len(sessions),
            "needs_human": sum(1 for s in sessions if s.needs_human),
            "human_controlled": sum(1 for s in sessions if s.human_controlled),
            "messages": sum(len(s.history) for s in sessions),
        }

    # --- 序列化 ---
    def _conv_summary(self, s: Session) -> dict:
        last = s.history[-1] if s.history else None
        return {
            "chat_id": s.chat_id,
            "human_controlled": s.human_controlled,
            "needs_human": s.needs_human,
            "escalation_reason": s.escalation_reason,
            "message_count": len(s.history),
            "last_message": last.content if last else "",
        }

    @staticmethod
    def _msg_dict(m: Message) -> dict:
        return {
            "msg_id": m.msg_id, "sender_id": m.sender_id, "sender_name": m.sender_name,
            "content": m.content, "msg_type": m.msg_type, "is_at_bot": m.is_at_bot,
            "timestamp": m.timestamp,
            "is_bot": m.sender_id == "__bot__", "is_human_agent": m.sender_id == HUMAN_SENDER_ID,
        }

    @staticmethod
    def _json(body: bytes) -> dict:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("body 必须是 JSON 对象")
        return data


def _make_handler(app: ApiApp):
    class Handler(BaseHTTPRequestHandler):
        def _run(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload = app.handle(method, self.path, body, headers)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._run("GET")

        def do_POST(self) -> None:
            self._run("POST")

        def log_message(self, *args) -> None:  # 静音默认访问日志
            pass

    return Handler


def build_app() -> ApiApp:
    from main import build_handler, load_env_file
    load_env_file()
    if os.environ.get("STORE") == "sqlite":
        from core.session_sqlite import SqliteSessionStore
        sessions = SqliteSessionStore(os.environ.get("SQLITE_PATH", "data/sessions.db"))
    else:
        from core.session import SessionStore
        sessions = SessionStore()
    token = os.environ.get("ADMIN_TOKEN", "")
    if not token:
        print("[api] 警告：未设置 ADMIN_TOKEN，鉴权已放行（仅限本地开发）", file=sys.stderr)
    return ApiApp(sessions, build_handler(), adapter=None, admin_token=token)


def main() -> None:
    app = build_app()
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), _make_handler(app))
    print(f"[api] 监听 http://{host}:{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
