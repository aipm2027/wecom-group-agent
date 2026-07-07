"""企业微信回调加解密(等价官方 WXBizMsgCrypt),纯 Python(含自实现 AES-256)。

严格遵循企业微信官方回调加解密标准,与腾讯服务器互通。
- EncodingAESKey 为 43 位字符;AESKey = base64.b64decode(EncodingAESKey + "=") 得到 32 字节。
- 算法 AES-256-CBC;IV = AESKey 前 16 字节。
- 明文打包:random16 + msg_len(4 字节大端) + msg_bytes + receiveid_bytes。
- 填充 PKCS#7,block_size = 32(腾讯规范)。
- 签名:SHA1(sorted([token, timestamp, nonce, encrypt])).hexdigest。
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
import xml.etree.ElementTree as ET
from typing import Optional

# ---------------------------------------------------------------------------
# AES-256 纯 Python 实现(FIPS-197 兼容)
# ---------------------------------------------------------------------------

_S_BOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]

_INV_S_BOX = [
    0x52, 0x09, 0x6A, 0xD5, 0x30, 0x36, 0xA5, 0x38, 0xBF, 0x40, 0xA3, 0x9E, 0x81, 0xF3, 0xD7, 0xFB,
    0x7C, 0xE3, 0x39, 0x82, 0x9B, 0x2F, 0xFF, 0x87, 0x34, 0x8E, 0x43, 0x44, 0xC4, 0xDE, 0xE9, 0xCB,
    0x54, 0x7B, 0x94, 0x32, 0xA6, 0xC2, 0x23, 0x3D, 0xEE, 0x4C, 0x95, 0x0B, 0x42, 0xFA, 0xC3, 0x4E,
    0x08, 0x2E, 0xA1, 0x66, 0x28, 0xD9, 0x24, 0xB2, 0x76, 0x5B, 0xA2, 0x49, 0x6D, 0x8B, 0xD1, 0x25,
    0x72, 0xF8, 0xF6, 0x64, 0x86, 0x68, 0x98, 0x16, 0xD4, 0xA4, 0x5C, 0xCC, 0x5D, 0x65, 0xB6, 0x92,
    0x6C, 0x70, 0x48, 0x50, 0xFD, 0xED, 0xB9, 0xDA, 0x5E, 0x15, 0x46, 0x57, 0xA7, 0x8D, 0x9D, 0x84,
    0x90, 0xD8, 0xAB, 0x00, 0x8C, 0xBC, 0xD3, 0x0A, 0xF7, 0xE4, 0x58, 0x05, 0xB8, 0xB3, 0x45, 0x06,
    0xD0, 0x2C, 0x1E, 0x8F, 0xCA, 0x3F, 0x0F, 0x02, 0xC1, 0xAF, 0xBD, 0x03, 0x01, 0x13, 0x8A, 0x6B,
    0x3A, 0x91, 0x11, 0x41, 0x4F, 0x67, 0xDC, 0xEA, 0x97, 0xF2, 0xCF, 0xCE, 0xF0, 0xB4, 0xE6, 0x73,
    0x96, 0xAC, 0x74, 0x22, 0xE7, 0xAD, 0x35, 0x85, 0xE2, 0xF9, 0x37, 0xE8, 0x1C, 0x75, 0xDF, 0x6E,
    0x47, 0xF1, 0x1A, 0x71, 0x1D, 0x29, 0xC5, 0x89, 0x6F, 0xB7, 0x62, 0x0E, 0xAA, 0x18, 0xBE, 0x1B,
    0xFC, 0x56, 0x3E, 0x4B, 0xC6, 0xD2, 0x79, 0x20, 0x9A, 0xDB, 0xC0, 0xFE, 0x78, 0xCD, 0x5A, 0xF4,
    0x1F, 0xDD, 0xA8, 0x33, 0x88, 0x07, 0xC7, 0x31, 0xB1, 0x12, 0x10, 0x59, 0x27, 0x80, 0xEC, 0x5F,
    0x60, 0x51, 0x7F, 0xA9, 0x19, 0xB5, 0x4A, 0x0D, 0x2D, 0xE5, 0x7A, 0x9F, 0x93, 0xC9, 0x9C, 0xEF,
    0xA0, 0xE0, 0x3B, 0x4D, 0xAE, 0x2A, 0xF5, 0xB0, 0xC8, 0xEB, 0xBB, 0x3C, 0x83, 0x53, 0x99, 0x61,
    0x17, 0x2B, 0x04, 0x7E, 0xBA, 0x77, 0xD6, 0x26, 0xE1, 0x69, 0x14, 0x63, 0x55, 0x21, 0x0C, 0x7D,
]

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法(俄罗斯农民法),用于 MixColumns。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a <<= 1
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


def _sub_bytes(state: list[list[int]]) -> None:
    for c in range(4):
        for r in range(4):
            state[c][r] = _S_BOX[state[c][r]]


def _inv_sub_bytes(state: list[list[int]]) -> None:
    for c in range(4):
        for r in range(4):
            state[c][r] = _INV_S_BOX[state[c][r]]


def _shift_rows(state: list[list[int]]) -> None:
    for r in range(1, 4):
        row = [state[c][r] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            state[c][r] = row[c]


def _inv_shift_rows(state: list[list[int]]) -> None:
    for r in range(1, 4):
        row = [state[c][r] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            state[c][r] = row[c]


def _mix_column(col: list[int]) -> list[int]:
    a, b, c, d = col
    return [
        _gmul(0x02, a) ^ _gmul(0x03, b) ^ c ^ d,
        a ^ _gmul(0x02, b) ^ _gmul(0x03, c) ^ d,
        a ^ b ^ _gmul(0x02, c) ^ _gmul(0x03, d),
        _gmul(0x03, a) ^ b ^ c ^ _gmul(0x02, d),
    ]


def _inv_mix_column(col: list[int]) -> list[int]:
    a, b, c, d = col
    return [
        _gmul(0x0E, a) ^ _gmul(0x0B, b) ^ _gmul(0x0D, c) ^ _gmul(0x09, d),
        _gmul(0x09, a) ^ _gmul(0x0E, b) ^ _gmul(0x0B, c) ^ _gmul(0x0D, d),
        _gmul(0x0D, a) ^ _gmul(0x09, b) ^ _gmul(0x0E, c) ^ _gmul(0x0B, d),
        _gmul(0x0B, a) ^ _gmul(0x0D, b) ^ _gmul(0x09, c) ^ _gmul(0x0E, d),
    ]


def _mix_columns(state: list[list[int]]) -> None:
    for c in range(4):
        state[c] = _mix_column(state[c])


def _inv_mix_columns(state: list[list[int]]) -> None:
    for c in range(4):
        state[c] = _inv_mix_column(state[c])


def _add_round_key(state: list[list[int]], round_key: list[list[int]]) -> None:
    for c in range(4):
        for r in range(4):
            state[c][r] ^= round_key[c][r]


def _key_expansion(key: bytes) -> list[list[list[int]]]:
    nk = len(key) // 4
    nr = {4: 10, 6: 12, 8: 14}[nk]
    w: list[list[int]] = []
    for i in range(nk):
        w.append([key[4 * i], key[4 * i + 1], key[4 * i + 2], key[4 * i + 3]])
    for i in range(nk, 4 * (nr + 1)):
        temp = w[i - 1][:]
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_S_BOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_S_BOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    round_keys = []
    for r in range(nr + 1):
        round_keys.append([w[4 * r + c] for c in range(4)])
    return round_keys


def _block_to_state(block: bytes) -> list[list[int]]:
    return [[block[r + 4 * c] for r in range(4)] for c in range(4)]


def _state_to_block(state: list[list[int]]) -> bytes:
    return bytes(state[c][r] for c in range(4) for r in range(4))


def _aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    state = _block_to_state(block)
    round_keys = _key_expansion(key)
    _add_round_key(state, round_keys[0])
    for i in range(1, len(round_keys) - 1):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[i])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, round_keys[-1])
    return _state_to_block(state)


def _aes_decrypt_block(key: bytes, block: bytes) -> bytes:
    state = _block_to_state(block)
    round_keys = _key_expansion(key)
    _add_round_key(state, round_keys[-1])
    for i in range(len(round_keys) - 2, 0, -1):
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        _add_round_key(state, round_keys[i])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _inv_sub_bytes(state)
    _add_round_key(state, round_keys[0])
    return _state_to_block(state)


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    ciphertext = b""
    prev = iv
    for i in range(0, len(plaintext), 16):
        block = plaintext[i : i + 16]
        block = bytes(a ^ b for a, b in zip(block, prev))
        enc = _aes_encrypt_block(key, block)
        ciphertext += enc
        prev = enc
    return ciphertext


def _aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    plaintext = b""
    prev = iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i : i + 16]
        dec = _aes_decrypt_block(key, block)
        plaintext += bytes(a ^ b for a, b in zip(dec, prev))
        prev = block
    return plaintext


# ---------------------------------------------------------------------------
# PKCS#7 填充(block_size = 32,腾讯规范)
# ---------------------------------------------------------------------------

def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes, block_size: int = 32) -> bytes:
    if len(data) == 0 or len(data) % block_size != 0:
        raise WeComCryptError("解密结果长度非法")
    pad = data[-1]
    if pad < 1 or pad > block_size:
        raise WeComCryptError("非法填充")
    if data[-pad:] != bytes([pad] * pad):
        raise WeComCryptError("非法填充")
    return data[:-pad]


# ---------------------------------------------------------------------------
# 明文打包/解包(random16 + msg_len + msg + receiveid)
# ---------------------------------------------------------------------------

def _pack(plain_bytes: bytes, receiveid_bytes: bytes) -> bytes:
    random16 = os.urandom(16)
    msg_len = struct.pack(">I", len(plain_bytes))
    return random16 + msg_len + plain_bytes + receiveid_bytes


def _unpack(packed_bytes: bytes, expected_receiveid_bytes: bytes) -> bytes:
    if len(packed_bytes) < 20:
        raise WeComCryptError("解密包长度不足")
    msg_len = struct.unpack(">I", packed_bytes[16:20])[0]
    msg_end = 20 + msg_len
    if msg_end > len(packed_bytes):
        raise WeComCryptError("消息长度非法")
    msg = packed_bytes[20:msg_end]
    receiveid = packed_bytes[msg_end:]
    if receiveid != expected_receiveid_bytes:
        raise WeComCryptError("receiveid 不匹配")
    return msg


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------

def _sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    arr = sorted([token, timestamp, nonce, encrypt])
    s = "".join(arr)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

class WeComCryptError(Exception):
    """企业微信加解密相关错误。"""


class WXBizMsgCrypt:
    """企业微信回调消息加解密(等价官方 WXBizMsgCrypt),纯 Python 标准库实现。"""

    def __init__(self, token: str, encoding_aes_key: str, receiveid: str) -> None:
        self.token = token
        try:
            self.aes_key = base64.b64decode(encoding_aes_key + "=")
        except Exception as exc:
            raise WeComCryptError("EncodingAESKey 非法") from exc
        if len(self.aes_key) != 32:
            raise WeComCryptError("AESKey 长度不是 32 字节")
        self.receiveid = receiveid
        self.iv = self.aes_key[:16]

    # --- 内部加解密 ---

    def _encrypt(self, plain_bytes: bytes) -> bytes:
        packed = _pack(plain_bytes, self.receiveid.encode("utf-8"))
        padded = _pkcs7_pad(packed, 32)
        return _aes_cbc_encrypt(self.aes_key, self.iv, padded)

    def _decrypt(self, cipher_bytes: bytes) -> bytes:
        decrypted = _aes_cbc_decrypt(self.aes_key, self.iv, cipher_bytes)
        unpadded = _pkcs7_unpad(decrypted, 32)
        return _unpack(unpadded, self.receiveid.encode("utf-8"))

    # --- 公共方法 ---

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """校验 URL 签名并解密 echostr,返回明文;失败抛出 WeComCryptError。"""
        sig = _sha1_signature(self.token, str(timestamp), str(nonce), echostr)
        if sig != msg_signature:
            raise WeComCryptError("URL 验证签名失败")
        try:
            cipher_bytes = base64.b64decode(echostr)
        except Exception as exc:
            raise WeComCryptError("echostr base64 解码失败") from exc
        msg = self._decrypt(cipher_bytes)
        return msg.decode("utf-8")

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, post_xml: str) -> str:
        """校验 POST 消息签名并解密,返回明文 XML;失败抛出 WeComCryptError。"""
        try:
            root = ET.fromstring(post_xml)
            encrypt_node = root.find("Encrypt")
            if encrypt_node is None or encrypt_node.text is None:
                raise WeComCryptError("XML 中缺少 Encrypt 节点")
            encrypt = encrypt_node.text
        except ET.ParseError as exc:
            raise WeComCryptError(f"XML 解析失败: {exc}") from exc
        sig = _sha1_signature(self.token, str(timestamp), str(nonce), encrypt)
        if sig != msg_signature:
            raise WeComCryptError("消息签名验证失败")
        try:
            cipher_bytes = base64.b64decode(encrypt)
        except Exception as exc:
            raise WeComCryptError("密文 base64 解码失败") from exc
        msg = self._decrypt(cipher_bytes)
        return msg.decode("utf-8")

    def encrypt_msg(self, reply_plain: str, nonce: str, timestamp: str) -> str:
        """将明文加密打包成响应 XML(含 Encrypt/MsgSignature/TimeStamp/Nonce)。"""
        plain_bytes = reply_plain.encode("utf-8")
        cipher_bytes = self._encrypt(plain_bytes)
        encrypt = base64.b64encode(cipher_bytes).decode("utf-8")
        sig = _sha1_signature(self.token, str(timestamp), str(nonce), encrypt)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
