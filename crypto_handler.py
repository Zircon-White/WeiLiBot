# crypto_handler.py
"""
企业微信消息加解密模块
基于企业微信官方算法封装
主人不需要看懂这里，直接用就行！
"""

import base64
import hashlib
import struct
import time
import random
import string
import xml.etree.ElementTree as ET
from Crypto.Cipher import AES


class WeChatCrypto:
    """企业微信消息加解密"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")  # AES密钥
        self.corp_id = corp_id

    # ── 验证签名 ──────────────────────────────────────
    def verify_signature(self, signature: str, timestamp: str, nonce: str, echo_str: str = "") -> bool:
        """验证来自企业微信的请求签名"""
        items = sorted([self.token, timestamp, nonce, echo_str])
        sha1 = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
        return sha1 == signature

    # ── 解密消息 ──────────────────────────────────────
    def decrypt_message(self, encrypted_msg: str) -> str:
        """解密企业微信发来的加密消息，返回原始XML字符串"""
        try:
            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            decrypted = cipher.decrypt(base64.b64decode(encrypted_msg))

            # 去掉随机字节和填充 ```python
            # 去掉随机字节和填充
            content = decrypted[16:]  # 前16字节是随机字符串
            msg_len = struct.unpack(">I", content[:4])[0]  # 取消息长度
            msg_content = content[4:4 + msg_len]
            return msg_content.decode("utf-8")

        except Exception as e:
            print(f"[解密失败] {e}")
            return ""

    # ── 加密消息 ──────────────────────────────────────
    def encrypt_message(self, reply_msg: str, timestamp: str, nonce: str) -> dict:
        """
        加密要回复给企业微信的消息
        返回包含加密内容、签名等的字典
        """
        # 构造明文：随机16字节 + 消息长度(4字节) + 消息内容 + CorpID
        random_str = "".join(random.choices(string.ascii_letters, k=16)).encode("utf-8")
        msg_bytes = reply_msg.encode("utf-8")
        msg_len = struct.pack(">I", len(msg_bytes))
        corp_id_bytes = self.corp_id.encode("utf-8")

        plain = random_str + msg_len + msg_bytes + corp_id_bytes

        # PKCS7补位
        block_size = 32
        pad_len = block_size - len(plain) % block_size
        plain += (chr(pad_len) * pad_len).encode("utf-8")

        # AES加密
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        encrypted = base64.b64encode(cipher.encrypt(plain)).decode("utf-8")

        # 生成签名
        items = sorted([self.token, timestamp, nonce, encrypted])
        signature = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

        return {
            "encrypted": encrypted,
            "signature": signature,
            "timestamp": timestamp,
            "nonce": nonce
        }

    # ── 解析XML消息体 ──────────────────────────────────
    @staticmethod
    def parse_xml(xml_str: str) -> dict:
        """把企业微信发来的XML消息解析成字典"""
        try:
            root = ET.fromstring(xml_str)
            result = {}
            for child in root:
                result[child.tag] = child.text or ""
            return result
        except ET.ParseError as e:
            print(f"[XML解析失败] {e}")
            return {}

    # ── 构造XML回复体 ──────────────────────────────────
    @staticmethod
    def build_reply_xml(to_user: str, from_user: str, content: str) -> str:
        """构造回复给企业微信的XML字符串"""
        timestamp = str(int(time.time()))
        return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""