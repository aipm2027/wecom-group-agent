"""Mock 适配器（Mac 可跑）。

用来在没有真实 hook 的环境下，把「消息进 → 路由 → 回复 → 发出」这条
全链路跑通、可重复验证。两种输入模式：

1. 交互模式（默认）：从 stdin 逐行读，格式：
       chat_id|sender|content
   例：  群A|张三|@bot 你好
   内容里含 `@bot` 视为 @ 了机器人（会被剥掉再进内容）。

2. 脚本模式：设置环境变量 MOCK_SCRIPT=xxx.json，读一个消息数组批量喂入，
   每条是一个 dict，可含 Message 的任意字段（缺省值见下）。用于自动化验证
   去重 / 限流 / 触发策略。

send() 直接打印到 stdout，模拟发消息成功。
"""
from __future__ import annotations

import json
import sys

from core.adapter import Adapter, OnMessage
from core.message import Message

AT_TOKEN = "@bot"


class MockCliAdapter(Adapter):
    def __init__(self, script_path: str | None = None) -> None:
        self.script_path = script_path
        self._seq = 0  # 自增，给交互消息生成 msg_id

    def send(self, chat_id: str, text: str) -> None:
        print(f"[发送→{chat_id}] {text}", flush=True)

    def start(self, on_message: OnMessage) -> None:
        if self.script_path:
            self._run_script(on_message)
        else:
            self._run_interactive(on_message)

    # --- 脚本模式 ---
    def _run_script(self, on_message: OnMessage) -> None:
        with open(self.script_path, encoding="utf-8") as f:
            items = json.load(f)
        for i, item in enumerate(items):
            msg = self._from_dict(item, i)
            print(f"[收到←{msg.chat_id}] {msg.sender_name}: {msg.content}"
                  f"{'  (@bot)' if msg.is_at_bot else ''}", flush=True)
            on_message(msg)

    def _from_dict(self, item: dict, idx: int) -> Message:
        content = item.get("content", "")
        return Message(
            chat_id=item.get("chat_id", "群A"),
            chat_type=item.get("chat_type", "group"),
            msg_id=str(item.get("msg_id", f"script-{idx}")),
            sender_id=item.get("sender_id", item.get("sender_name", "u")),
            sender_name=item.get("sender_name", "某人"),
            content=content,
            msg_type=item.get("msg_type", "text"),
            is_at_bot=bool(item.get("is_at_bot", AT_TOKEN in content)),
            timestamp=int(item.get("timestamp", 0)),
            raw=item,
        )

    # --- 交互模式 ---
    def _run_interactive(self, on_message: OnMessage) -> None:
        print("交互模式。格式：chat_id|sender|content（内容含 @bot 视为 @ 机器人）。"
              "Ctrl-D 退出。", flush=True)
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                print("  ⚠ 格式应为 chat_id|sender|content", flush=True)
                continue
            chat_id, sender, content = (p.strip() for p in parts)
            is_at = AT_TOKEN in content
            clean = content.replace(AT_TOKEN, "").strip()
            self._seq += 1
            on_message(Message(
                chat_id=chat_id,
                chat_type="group",
                msg_id=f"cli-{self._seq}",
                sender_id=sender,
                sender_name=sender,
                content=clean,
                is_at_bot=is_at,
                raw={"line": line},
            ))
