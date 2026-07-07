"""微信客服适配器离线测试。

不联网、不需要真实密钥，通过注入假 HTTP 客户端与假回调验证全链路。
"""
from __future__ import annotations

import base64
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.wecom_crypto import (
    WXBizMsgCrypt,
    WeComCryptError,
    _aes_encrypt_block,
    _aes_decrypt_block,
    _pkcs7_pad,
    _pkcs7_unpad,
)
from adapters.wecom_kf import WecomKfAdapter
from core.message import Message

# ---------------------------------------------------------------------------
# 固定测试配置
# ---------------------------------------------------------------------------

_TOKEN = "TESTtoken123"
_RECEIVEID = "wwtestcorpid"
_ENCODING_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"


def test_encoding_aes_key_valid() -> None:
    """EncodingAESKey 必须能 base64 解码为 32 字节。"""
    decoded = base64.b64decode(_ENCODING_AES_KEY + "=")
    assert len(decoded) == 32, f"期望 32 字节,实际 {len(decoded)}"


def test_aes_block_fips197() -> None:
    """FIPS-197 AES-256 单块向量断言。"""
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    ct = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    assert _aes_encrypt_block(key, pt) == ct, "AES-256 加密结果不匹配"
    assert _aes_decrypt_block(key, ct) == pt, "AES-256 解密结果不匹配"


def test_pkcs7() -> None:
    """PKCS#7 填充/去填充(block_size=32)。"""
    data = b"hello"
    padded = _pkcs7_pad(data, 32)
    assert len(padded) % 32 == 0
    assert _pkcs7_unpad(padded, 32) == data
    # 刚好整倍数
    data2 = b"x" * 32
    padded2 = _pkcs7_pad(data2, 32)
    assert len(padded2) == 64
    assert _pkcs7_unpad(padded2, 32) == data2


def test_crypt_roundtrip() -> None:
    """WXBizMsgCrypt encrypt_msg -> decrypt_msg 能还原原明文。"""
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)
    plain = "hello world 你好"
    nonce = "nonce123"
    timestamp = "1234567890"
    xml = crypt.encrypt_msg(plain, nonce, timestamp)
    root = ET.fromstring(xml)
    encrypt = root.find("Encrypt").text
    msg_sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce_out = root.find("Nonce").text
    post_xml = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"
    decrypted = crypt.decrypt_msg(msg_sig, ts, nonce_out, post_xml)
    assert decrypted == plain


def test_crypt_tamper() -> None:
    """篡改签名或密文应抛出 WeComCryptError。"""
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)
    plain = "test"
    xml = crypt.encrypt_msg(plain, "nonce", "123")
    root = ET.fromstring(xml)
    encrypt = root.find("Encrypt").text
    msg_sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce = root.find("Nonce").text

    post_xml = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"

    # 篡改签名
    try:
        crypt.decrypt_msg("bad_sig", ts, nonce, post_xml)
        assert False, "应抛出 WeComCryptError"
    except WeComCryptError:
        pass

    # 篡改密文
    bad_encrypt = encrypt[:-1] + ("A" if encrypt[-1] != "A" else "B")
    bad_post_xml = f"<xml><Encrypt><![CDATA[{bad_encrypt}]]></Encrypt></xml>"
    try:
        crypt.decrypt_msg(msg_sig, ts, nonce, bad_post_xml)
        assert False, "应抛出 WeComCryptError"
    except WeComCryptError:
        pass


def test_verify_url_roundtrip() -> None:
    """用 encrypt_msg 内部逻辑造出合法 echostr 场景,verify_url 能还原明文;签名错误则失败。"""
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)
    plain = "1234567890"
    nonce = "nonce123"
    timestamp = "1234567890"
    xml = crypt.encrypt_msg(plain, nonce, timestamp)
    root = ET.fromstring(xml)
    echostr = root.find("Encrypt").text
    msg_sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce_out = root.find("Nonce").text

    result = crypt.verify_url(msg_sig, ts, nonce_out, echostr)
    assert result == plain

    # 签名错误
    try:
        crypt.verify_url("bad_sig", ts, nonce_out, echostr)
        assert False, "应抛出 WeComCryptError"
    except WeComCryptError:
        pass


def test_adapter_post() -> None:
    """注入假 sync_msg 与假 access_token,驱动 POST 处理逻辑,断言 on_message 收到正确 Message。"""
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)

    event_xml = (
        "<xml>"
        "<ToUserName><![CDATA[wwtestcorpid]]></ToUserName>"
        "<FromUserName><![CDATA[sys]]></FromUserName>"
        "<CreateTime>123456789</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[kf_msg_or_event]]></Event>"
        "<Token><![CDATA[token001]]></Token>"
        "<OpenKfId><![CDATA[kf001]]></OpenKfId>"
        "</xml>"
    )

    encrypted_xml = crypt.encrypt_msg(event_xml, "nonce123", "1234567890")
    root = ET.fromstring(encrypted_xml)
    encrypt = root.find("Encrypt").text
    msg_sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce_out = root.find("Nonce").text
    post_body = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt><AgentID>0</AgentID></xml>"

    def fake_http_get_json(url: str) -> dict:
        if "gettoken" in url:
            return {"access_token": "fake_token", "expires_in": 7200}
        return {}

    def fake_http_post_json(url: str, payload: dict) -> dict:
        if "sync_msg" in url:
            return {
                "next_cursor": "cursor001",
                "msg_list": [
                    {
                        "msgid": "msg001",
                        "open_kfid": "kf001",
                        "external_userid": "user001",
                        "msgtype": "text",
                        "text": {"content": "你好"},
                    }
                ],
            }
        if "send_msg" in url:
            return {"errcode": 0}
        return {}

    adapter = WecomKfAdapter(
        corp_id=_RECEIVEID,
        kf_secret="fake_secret",
        callback_token=_TOKEN,
        encoding_aes_key=_ENCODING_AES_KEY,
        http_get_json=fake_http_get_json,
        http_post_json=fake_http_post_json,
    )

    received: list[Message] = []

    def on_message(msg: Message) -> None:
        received.append(msg)

    adapter._handle_post(post_body, msg_sig, ts, nonce_out, on_message)
    assert len(received) == 1
    msg = received[0]
    assert isinstance(msg, Message)
    assert msg.chat_type == "single"
    assert msg.is_at_bot is True
    assert msg.sender_id == "user001"
    assert msg.chat_id == "kf001:user001"
    assert msg.content == "你好"
    assert msg.msg_type == "text"
    assert msg.msg_id == "msg001"
    assert msg.timestamp == 0


def test_adapter_send() -> None:
    """注入假 send 客户端,调 send("kfid:userid","你好"),断言下发 payload 结构正确。"""
    sent_payloads: list[tuple[str, dict]] = []

    def fake_http_get_json(url: str) -> dict:
        if "gettoken" in url:
            return {"access_token": "fake_token", "expires_in": 7200}
        return {}

    def fake_http_post_json(url: str, payload: dict) -> dict:
        sent_payloads.append((url, payload))
        return {"errcode": 0}

    adapter = WecomKfAdapter(
        corp_id="wwtestcorpid",
        kf_secret="fake_secret",
        callback_token="token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        http_get_json=fake_http_get_json,
        http_post_json=fake_http_post_json,
    )

    adapter.send("kf001:user001", "你好")
    assert len(sent_payloads) == 1
    url, payload = sent_payloads[0]
    assert payload["touser"] == "user001"
    assert payload["open_kfid"] == "kf001"
    assert payload["msgtype"] == "text"
    assert payload["text"]["content"] == "你好"


def test_adapter_error_handling() -> None:
    """构造会抛错的假 HTTP,断言相关方法被 try/except 兜住不崩(降级)。"""
    def ok_get_json(url: str) -> dict:
        return {"access_token": "fake_token", "expires_in": 7200}

    def crash_post_json(url: str, payload: dict) -> dict:
        raise RuntimeError("网络爆炸")

    adapter = WecomKfAdapter(
        corp_id="wwtestcorpid",
        kf_secret="fake_secret",
        callback_token="token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        http_get_json=ok_get_json,
        http_post_json=crash_post_json,
    )

    # _get_access_token 应正常返回 token
    token = adapter._get_access_token()
    assert token == "fake_token"

    # send() 内 _send_text 会调用 crash_post_json,但 send() 捕获异常,不崩溃
    try:
        adapter.send("kf001:user001", "hello")
    except Exception:
        assert False, "send 不应抛出异常"

    # 换 crash_get_json 测试 _sync_msg / _handle_post 不崩
    def crash_get_json(url: str) -> dict:
        raise RuntimeError("网络爆炸")

    adapter2 = WecomKfAdapter(
        corp_id="wwtestcorpid",
        kf_secret="fake_secret",
        callback_token="token",
        encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        http_get_json=crash_get_json,
        http_post_json=crash_post_json,
    )

    # _get_access_token 被兜住,返回空字符串
    assert adapter2._get_access_token() == ""
    # _sync_msg 被兜住,返回空列表
    assert adapter2._sync_msg("token001", "") == []
    # _handle_post 被兜住,不抛异常
    try:
        adapter2._handle_post("<xml></xml>", "sig", "ts", "nonce", lambda msg: None)
    except Exception:
        assert False, "_handle_post 不应抛出异常"


def main() -> None:
    for fn in (
        test_encoding_aes_key_valid,
        test_aes_block_fips197,
        test_pkcs7,
        test_crypt_roundtrip,
        test_crypt_tamper,
        test_verify_url_roundtrip,
        test_adapter_post,
        test_adapter_send,
        test_adapter_error_handling,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n微信客服适配器离线测试全部通过！")


if __name__ == "__main__":
    main()
