"""ntwork 真实 hook 适配器 —— stub，本阶段不实现。

真实接管微信群对话只能靠 hook 注入企微客户端，且有以下硬约束：
  - 仅 Windows：ntwork 注入的是 Windows 版企微客户端 WXWork.exe，Mac 跑不了。
  - 需要 `pip install ntwork`，并在同机登录企微客户端。
  - 违反企业微信用户协议，有封号风险（用户已知情）。

下一阶段（Windows）实现要点：
  1. start():
       import ntwork
       wework = ntwork.WeWork()
       wework.open()                      # 拉起/附着企微客户端，扫码登录
       @wework.msg_register(ntwork.MT_RECV_TEXT_MSG)   # 群/单聊文本消息
       def _on(wework, event):
           msg = _map_event(event)        # 见下，映射成 core.Message
           on_message(msg)
       wework.wait_login(); ...           # 进事件循环阻塞
  2. _map_event(event) 字段映射（以 ntwork 事件 data 为准，需实机核对）：
       chat_id     <- data["conversation_id"]（群会话 id）
       chat_type   <- "group" if 群会话 else "single"
       msg_id      <- data["local_id"] / data["msgid"]
       sender_id   <- data["sender"]
       sender_name <- 通讯录/群成员查询补全
       content     <- data["content"]
       is_at_bot   <- 解析 content / at 列表里是否含自己
  3. send(chat_id, text):
       wework.send_text(conversation_id=chat_id, content=text)

核心 / 路由 / handler 全部复用，届时只实现本文件。
"""
from __future__ import annotations

from core.adapter import Adapter, OnMessage

_UNAVAILABLE = (
    "ntwork 真实 hook 适配器尚未实现，且只能在 Windows + 企微客户端上运行。"
    "本地验证请用 MOCK=1 走 MockCliAdapter。实现要点见本文件顶部注释。"
)


class NtworkWecomAdapter(Adapter):
    def start(self, on_message: OnMessage) -> None:
        raise NotImplementedError(_UNAVAILABLE)

    def send(self, chat_id: str, text: str) -> None:
        raise NotImplementedError(_UNAVAILABLE)
