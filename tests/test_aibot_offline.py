"""智能机器人长连接适配器离线测试:帧编解码 + 协议逻辑,注入假 WS,不碰网络。"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("WECOM_AIBOT_ID", "bot-test")
os.environ.setdefault("WECOM_AIBOT_SECRET", "secret-test")

from adapters.wecom_aibot import (_OP_TEXT, WecomAibotAdapter,  # noqa: E402
                                  WecomAibotError, _encode_frame, _read_frame)


# ── 帧编解码 ──────────────────────────────────────────────────

def test_frame_roundtrip() -> None:
    for payload in (b"", "hi".encode(), "中文消息🛍️".encode(), os.urandom(200), os.urandom(70000)):
        raw = _encode_frame(_OP_TEXT, payload)
        buf = io.BytesIO(raw)
        op, fin, out = _read_frame(lambda n: buf.read(n))
        assert op == _OP_TEXT and fin and out == payload
    print("test_frame_roundtrip ok")


def test_frame_client_mask_bit_set() -> None:
    raw = _encode_frame(_OP_TEXT, b"x")
    assert raw[1] & 0x80, "客户端帧必须置掩码位(RFC 6455 §5.3)"
    print("test_frame_client_mask_bit_set ok")


def test_frame_oversize_rejected() -> None:
    # 伪造 8 字节扩展长度声明超大帧
    fake = bytes([0x81, 127]) + (10 * 1024 * 1024 * 1024).to_bytes(8, "big")
    buf = io.BytesIO(fake)
    try:
        _read_frame(lambda n: buf.read(n))
        raise AssertionError("超大帧应被拒绝")
    except WecomAibotError:
        pass
    print("test_frame_oversize_rejected ok")


# ── 假 WS:脚本化服务端帧序列 ─────────────────────────────────

class FakeWS:
    def __init__(self, incoming):
        self.incoming = list(incoming)  # 每项: str(文本帧) 或 None(close) 或 'TIMEOUT'
        self.sent: list[dict] = []
        self.closed = False

    def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv_text(self):
        if not self.incoming:
            raise socket.timeout()
        item = self.incoming.pop(0)
        if item == "TIMEOUT":
            raise socket.timeout()
        return item

    def settimeout(self, t) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _ok(req_id: str = "r0") -> str:
    return json.dumps({"headers": {"req_id": req_id}, "errcode": 0, "errmsg": "ok"})


def _msg_cb(req_id: str, content: str, chattype: str = "single",
            userid: str = "zhang", chatid: str = "") -> str:
    body = {"msgid": f"m-{req_id}", "aibotid": "bot-test", "chattype": chattype,
            "from": {"userid": userid}, "msgtype": "text", "text": {"content": content}}
    if chatid:
        body["chatid"] = chatid
    return json.dumps({"cmd": "aibot_msg_callback", "headers": {"req_id": req_id}, "body": body})


def _run_adapter(frames):
    """跑一轮 start():假 WS 帧耗尽抛 timeout 后由测试主动 stop。返回 (adapter, ws, 收到的消息)。"""
    ws = FakeWS(frames)
    adapter = WecomAibotAdapter(ws_factory=lambda: ws)
    got = []

    def on_message(m):
        got.append(m)
        adapter.send(m.chat_id, f"回复:{m.content}")
        adapter.stop()  # 处理完即停,避免测试悬挂

    t = threading.Thread(target=adapter.start, args=(on_message,), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "适配器未按期退出"
    return adapter, ws, got


def test_subscribe_and_reply_flow() -> None:
    _, ws, got = _run_adapter([_ok(), _msg_cb("req-1", "坚果怎么卖")])
    # 帧序:订阅 → (收回调) → 回复
    assert ws.sent[0]["cmd"] == "aibot_subscribe"
    assert ws.sent[0]["body"] == {"bot_id": "bot-test", "secret": "secret-test"}
    assert len(got) == 1 and got[0].content == "坚果怎么卖" and got[0].chat_type == "single"
    assert got[0].chat_id == "aibot-single-zhang", "单聊以发送者为会话键"
    reply = [f for f in ws.sent if f["cmd"] == "aibot_respond_msg"][0]
    assert reply["headers"]["req_id"] == "req-1", "回复必须透传回调 req_id"
    st = reply["body"]["stream"]
    assert st["finish"] is True and st["content"] == "回复:坚果怎么卖" and st["id"]
    print("test_subscribe_and_reply_flow ok")


def test_group_message_strips_mention() -> None:
    _, ws, got = _run_adapter([_ok(), _msg_cb("req-2", "@咕咕嘎嘎 有优惠吗",
                                              chattype="group", chatid="wr-group-1")])
    m = got[0]
    assert m.chat_id == "wr-group-1" and m.chat_type == "group" and m.is_at_bot
    assert m.content == "有优惠吗", f"应剥掉@提及,实际 {m.content!r}"
    print("test_group_message_strips_mention ok")


def test_subscribe_failure_raises_and_no_dispatch() -> None:
    bad = json.dumps({"headers": {"req_id": "r"}, "errcode": 853000, "errmsg": "invalid"})
    ws = FakeWS([bad])
    adapter = WecomAibotAdapter(ws_factory=lambda: ws)
    got = []

    def on_message(m):
        got.append(m)

    # 订阅失败 → 走重连退避;为测试可控,失败一次后立即 stop
    orig_sub = adapter._subscribe

    def sub_once():
        try:
            orig_sub()
        finally:
            adapter.stop()
    adapter._subscribe = sub_once
    t = threading.Thread(target=adapter.start, args=(on_message,), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive() and not got and ws.closed
    print("test_subscribe_failure_raises_and_no_dispatch ok")


def test_send_without_context_is_noop() -> None:
    ws = FakeWS([])
    adapter = WecomAibotAdapter(ws_factory=lambda: ws)
    adapter._ws = ws
    adapter.send("no-such-chat", "hello")  # 无回调上下文:不抛异常、不发帧
    assert not [f for f in ws.sent if f["cmd"] == "aibot_respond_msg"]
    print("test_send_without_context_is_noop ok")


def test_non_text_callback_ignored() -> None:
    img = json.dumps({"cmd": "aibot_msg_callback", "headers": {"req_id": "r-img"},
                      "body": {"msgid": "m1", "chattype": "single", "from": {"userid": "u"},
                               "msgtype": "image", "image": {"url": "U", "aeskey": "K"}}})
    ws = FakeWS([_ok(), img, "TIMEOUT"])
    adapter = WecomAibotAdapter(ws_factory=lambda: ws)
    got = []

    def on_message(m):
        got.append(m)
    # 图片回调被忽略后队列耗尽抛 timeout → 手动停
    def stop_soon():
        import time
        time.sleep(0.5)
        adapter.stop()
    threading.Thread(target=stop_soon, daemon=True).start()
    t = threading.Thread(target=adapter.start, args=(on_message,), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not got, "非文本消息不应投递"
    print("test_non_text_callback_ignored ok")


def test_missing_env_raises() -> None:
    old_id = os.environ.pop("WECOM_AIBOT_ID")
    try:
        WecomAibotAdapter()
        raise AssertionError("缺配置应报错")
    except RuntimeError:
        pass
    finally:
        os.environ["WECOM_AIBOT_ID"] = old_id
    print("test_missing_env_raises ok")


def test_outbox_bridge_roundtrip() -> None:
    """人工回复跨进程桥接:无 ws 进程 send→入 outbox;有 ws 的 agent 轮询代发并清队列。"""
    import tempfile
    tf = tempfile.mktemp(suffix=".jsonl")
    os.environ["WECOM_AIBOT_OUTBOX"] = tf
    try:
        # ① api_server 侧(无长连接):send 不再抛错,而是入 outbox
        offline_side = WecomAibotAdapter(ws_factory=lambda: None)
        offline_side.send("aibot-single-zhang", "人工:已帮你补发")
        with open(tf, encoding="utf-8") as f:
            queued = [json.loads(ln) for ln in f.read().splitlines()]
        assert queued and queued[0]["chat_id"] == "aibot-single-zhang", queued

        # ② agent 侧(有 ws + 回调上下文):_drain_outbox 经真实下发分支代发
        ws = FakeWS([])
        agent_side = WecomAibotAdapter(ws_factory=lambda: ws)
        agent_side._ws = ws
        agent_side._reply_ctx["aibot-single-zhang"] = ("req-1", time.time())
        agent_side._drain_outbox()
        replies = [m for m in ws.sent if m.get("cmd") == "aibot_respond_msg"]
        assert len(replies) == 1, f"应代发 1 条,实际 {len(replies)}"
        assert replies[0]["body"]["stream"]["content"] == "人工:已帮你补发"
        assert replies[0]["headers"]["req_id"] == "req-1", "必须透传回调 req_id"
        assert not os.path.exists(tf) and not os.path.exists(tf + ".sending"), "队列应被清空"
        # ③ 空队列再排一次不炸
        agent_side._drain_outbox()
        # ④ 重放边界(#36-C②):过期条目(排队超时效)丢弃不发,防 agent 重启后重放旧人工回复
        with open(tf, "w", encoding="utf-8") as f:
            f.write(json.dumps({"chat_id": "aibot-single-zhang", "text": "昨天的旧回复",
                                "ts": time.time() - 999999}, ensure_ascii=False) + "\n")
        ws.sent.clear()
        agent_side._drain_outbox()
        assert not [m for m in ws.sent if m.get("cmd") == "aibot_respond_msg"], "过期条目不得代发"
    finally:
        os.environ.pop("WECOM_AIBOT_OUTBOX", None)
        for p in (tf, tf + ".sending"):
            if os.path.exists(p):
                os.unlink(p)
    print("test_outbox_bridge_roundtrip ok")


def main() -> None:
    for fn in (
        test_frame_roundtrip,
        test_frame_client_mask_bit_set,
        test_frame_oversize_rejected,
        test_subscribe_and_reply_flow,
        test_group_message_strips_mention,
        test_subscribe_failure_raises_and_no_dispatch,
        test_send_without_context_is_noop,
        test_non_text_callback_ignored,
        test_missing_env_raises,
        test_outbox_bridge_roundtrip,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nwecom_aibot 离线测试全部通过！")


if __name__ == "__main__":
    main()
