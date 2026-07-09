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
        cursor_path=os.devnull,
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


def test_sync_msg_errcode() -> None:
    """sync_msg 返回 errcode != 0 时，不把错误响应当消息，返回空列表（不静默误判/丢消息）。"""
    def ok_get(url: str) -> dict:
        return {"access_token": "fake_token", "expires_in": 7200}

    def err_post(url: str, payload: dict) -> dict:
        if "sync_msg" in url:
            return {"errcode": 40001, "errmsg": "invalid credential"}
        return {"errcode": 0}

    adapter = WecomKfAdapter(
        corp_id=_RECEIVEID, kf_secret="fake_secret", callback_token=_TOKEN,
        encoding_aes_key=_ENCODING_AES_KEY, http_get_json=ok_get, http_post_json=err_post,
    )
    assert adapter._sync_msg("token001", "") == [], "errcode 非 0 时应返回空列表而非把错误响应当消息"


def test_missing_aes_key_friendly_error() -> None:
    """未配置 EncodingAESKey 时构造适配器应抛带字段名的 RuntimeError（而非晦涩的 crypto 报错）。"""
    try:
        WecomKfAdapter(corp_id="c", kf_secret="s", callback_token="t", encoding_aes_key="")
        assert False, "应抛 RuntimeError"
    except RuntimeError as exc:
        assert "WECOM_ENCODING_AES_KEY" in str(exc), "错误信息应提示缺少的环境变量名"


def test_decrypt_bad_length() -> None:
    """密文长度非法（非 16 倍数）但签名正确时，应抛受控的 WeComCryptError 而非 IndexError。"""
    import hashlib
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)
    bad_encrypt = base64.b64encode(b"hello").decode("utf-8")  # 5 字节，非 16 倍数
    ts, nonce = "123", "n"
    sig = hashlib.sha1("".join(sorted([_TOKEN, ts, nonce, bad_encrypt])).encode("utf-8")).hexdigest()
    post_xml = f"<xml><Encrypt><![CDATA[{bad_encrypt}]]></Encrypt></xml>"
    try:
        crypt.decrypt_msg(sig, ts, nonce, post_xml)
        assert False, "应抛 WeComCryptError"
    except WeComCryptError:
        pass


def test_origin_filter_skips_bot_messages() -> None:
    """sync_msg 返回接待人员/机器人自己发的消息(origin=5)应被跳过，防自问自答循环。"""
    crypt = WXBizMsgCrypt(_TOKEN, _ENCODING_AES_KEY, _RECEIVEID)
    event_xml = "<xml><Token><![CDATA[tk]]></Token><OpenKfId><![CDATA[kf001]]></OpenKfId></xml>"
    enc = crypt.encrypt_msg(event_xml, "n", "1")
    root = ET.fromstring(enc)
    encrypt = root.find("Encrypt").text
    sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce = root.find("Nonce").text
    post = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"

    def get_ok(u: str) -> dict:
        return {"access_token": "t", "expires_in": 7200}

    def post_sync(u: str, p: dict) -> dict:
        return {"msg_list": [
            {"msgid": "m-user", "origin": 3, "open_kfid": "kf001", "external_userid": "u1",
             "msgtype": "text", "text": {"content": "你好"}},
            {"msgid": "m-bot", "origin": 5, "open_kfid": "kf001", "external_userid": "u1",
             "msgtype": "text", "text": {"content": "机器人自己发的"}},
        ]}

    adapter = WecomKfAdapter(corp_id=_RECEIVEID, kf_secret="s", callback_token=_TOKEN,
                             encoding_aes_key=_ENCODING_AES_KEY, cursor_path=os.devnull,
                             http_get_json=get_ok, http_post_json=post_sync)
    got: list = []
    ok = adapter._handle_post(post, sig, ts, nonce, lambda m: got.append(m))
    assert ok is True, "_handle_post 成功应返回 True"
    assert len(got) == 1 and got[0].content == "你好", "只应投递 origin=3 客户消息，跳过 origin=5 的 bot 消息"


def test_handle_post_returns_false_on_decrypt_fail() -> None:
    """解密失败时 _handle_post 返回 False（调用方据此回非 200 让腾讯重试，避免消息永久丢失）。"""
    adapter = WecomKfAdapter(corp_id=_RECEIVEID, kf_secret="s", callback_token=_TOKEN,
                             encoding_aes_key=_ENCODING_AES_KEY, cursor_path=os.devnull,
                             http_get_json=lambda u: {}, http_post_json=lambda u, p: {})
    ok = adapter._handle_post("<xml><Encrypt><![CDATA[bad]]></Encrypt></xml>", "sig", "ts", "n", lambda m: None)
    assert ok is False


def test_access_token_cached() -> None:
    """access_token 命中缓存：连续两次只应请求一次 gettoken。"""
    calls = {"n": 0}

    def get_token(u: str) -> dict:
        if "gettoken" in u:
            calls["n"] += 1
        return {"access_token": "t", "expires_in": 7200}

    adapter = WecomKfAdapter(corp_id=_RECEIVEID, kf_secret="s", callback_token=_TOKEN,
                             encoding_aes_key=_ENCODING_AES_KEY, cursor_path=os.devnull,
                             http_get_json=get_token, http_post_json=lambda u, p: {})
    assert adapter._get_access_token() == "t"
    assert adapter._get_access_token() == "t"
    assert calls["n"] == 1, "第二次应命中缓存，不再请求 gettoken"


def test_access_token_invalidated_on_errcode() -> None:
    """sync_msg 返回 errcode=40001(token 失效)后应清空缓存，下次强制重取，而非 2 小时内复用坏 token。"""
    calls = {"n": 0}

    def get_token(u: str) -> dict:
        calls["n"] += 1
        return {"access_token": f"t{calls['n']}", "expires_in": 7200}

    def post_expired(u: str, p: dict) -> dict:
        return {"errcode": 40001, "errmsg": "invalid access_token"}

    adapter = WecomKfAdapter(corp_id=_RECEIVEID, kf_secret="s", callback_token=_TOKEN,
                             encoding_aes_key=_ENCODING_AES_KEY, cursor_path=os.devnull,
                             http_get_json=get_token, http_post_json=post_expired)
    adapter._sync_msg("tk", "")   # gettoken(1) + sync 返回 40001 → 清缓存
    adapter._sync_msg("tk", "")   # 缓存已清 → 重新 gettoken(2)
    assert calls["n"] == 2, "token 失效后应强制重取"


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
        test_sync_msg_errcode,
        test_missing_aes_key_friendly_error,
        test_decrypt_bad_length,
        test_origin_filter_skips_bot_messages,
        test_handle_post_returns_false_on_decrypt_fail,
        test_access_token_cached,
        test_access_token_invalidated_on_errcode,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n微信客服适配器离线测试全部通过！")


if __name__ == "__main__":
    main()
