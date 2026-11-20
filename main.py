# main.py
"""
Flask Web服务
负责接收企业微信的回调请求
"""

import time
import threading
from flask import Flask, request, jsonify, make_response
import xml.etree.ElementTree as ET

from config import load_config
from crypto_handler import WeChatCrypto
from wechat_handler import send_message, download_media
from command_handler import try_handle_command
from ai_handler import handle_chat, handle_confirm, handle_reminder

app = Flask(__name__)

# ════════════════════════════════════════
#  初始化加解密实例（懒加载，等config填好再初始化）
# ════════════════════════════════════════

_crypto: WeChatCrypto = None

def get_crypto() -> WeChatCrypto:
    """懒加载加解密实例"""
    global _crypto
    if _crypto is None:
        config = load_config()
        _crypto = WeChatCrypto(
            token=config["wechat_token"],
            encoding_aes_key=config["wechat_aes_key"],
            corp_id=config["corp_id"]
        )
    return _crypto


# ════════════════════════════════════════
#  核心消息处理（异步，不阻塞回调响应）
# ════════════════════════════════════════

def _process_message(user_id: str, msg_type: str, content: str, media_id: str = ""):
    """
    在子线程里处理消息，避免超过企业微信5秒回调超时限制
    处理完再主动调用 send_message 把结果发给主人
    """
    try:
        image_data = None

        # ── 如果是图片消息，先下载图片 ──
        if msg_type == "image" and media_id:
            image_data = download_media(media_id)
            if image_data is None:
                send_message(user_id, "⚠️ 图片下载失败，请重新发送一次试试～")
                return

        # ── 文字消息或图片消息统一走这个入口 ──
        reply = handle_incoming_message(
            user_id=user_id,
            message=content,
            image_data=image_data
        )

        # ── 发送回复 ──
        send_message(user_id, reply)

    except Exception as e:
        print(f"[处理异常] {e}")
        send_message(user_id, "🦞 阿离刚才出了个小bug，请主人稍后再试！")


def handle_incoming_message(
    user_id: str,
    message: str,
    image_data: bytes = None
) -> str:
    """
    统一消息处理入口（复用B步骤的逻辑）
    """
    # ── 第一道关卡：指令拦截器 ──
    handled, reply = try_handle_command(message)
    if handled:
        print(f"[指令] {message}")
        return reply

    # ── 第二道关卡：y/Y 触发填表员 ──
    if message.strip() in ("y", "Y"):
        print("[归档] 触发填表员流程")
        return handle_confirm(user_id)

    # ── 第三道关卡：普通消息交给阿离 ──
    print(f"[对话] 转交AI处理，含图片：{image_data is not None}")
    return handle_chat(message, image_data)


# ════════════════════════════════════════
#  🌟 企业微信回调路由
# ════════════════════════════════════════

@app.route("/wechat/callback", methods=["GET", "POST"])
def wechat_callback():
    """企业微信统一回调入口"""

    crypto = get_crypto()
    config = load_config()

    # ────────────────────────────────────
    #  GET请求：企业微信验证服务器有效性
    # ────────────────────────────────────
    if request.method == "GET":
        signature = request.args.get("msg_signature", "")
        timestamp  = request.args.get("timestamp", "")
        nonce      = request.args.get("nonce", "")
        echo_str   = request.args.get("echostr", "")

        print(f"[验证] 收到企业微信服务器验证请求")

        # 验证签名
        if not crypto.verify_signature(signature, timestamp, nonce, echo_str):
            print("[验证] ❌ 签名验证失败")
            return make_response("Invalid signature", 403)

        # 解密 echostr 并原样返回
        decrypted = crypto.decrypt_message(echo_str)
        if not decrypted:
            print("[验证] ❌ echostr 解密失败")
            return make_response("Decrypt failed", 500)

        print("[验证] ✅ 服务器验证成功！")
        return make_response(decrypted, 200)

    # ────────────────────────────────────
    #  POST请求：接收用户发来的消息
    # ────────────────────────────────────
    if request.method == "POST":
        signature = request.args.get("msg_signature", "")
        timestamp  = request.args.get("timestamp", "")
        nonce      = request.args.get("nonce", "")

        # Step1：验证签名
        raw_body = request.data.decode("utf-8")

        # Step2：从XML里取出加密消息
        try:
            root = ET.fromstring(raw_body)
            encrypted_msg = root.find("Encrypt").text
        except Exception as e:
            print(f"[回调] XML解析失败：{e}")
            return make_response("success", 200)

        # Step3：验证签名（注意POST验证要带上encrypt内容）
        if not crypto.verify_signature(signature, timestamp, nonce, encrypted_msg):
            print("[回调] ❌ 签名验证失败")
            return make_response("success", 200)  # 企业微信要求即使失败也返回success

        # Step4：解密消息体
        decrypted_xml = crypto.decrypt_message(encrypted_msg)
        if not decrypted_xml:
            print("[回调] ❌ 消息解密失败")
            return make_response("success", 200)

        # Step5：解析XML，提取消息字段
        msg = WeChatCrypto.parse_xml(decrypted_xml)
        print(f"[回调] 收到消息：{msg}")

        msg_type  = msg.get("MsgType", "")
        user_id   = msg.get("FromUserName", "")
        content   = msg.get("Content", "")       # 文字消息内容
        media_id  = msg.get("MediaId", "")       # 图片/文件的media_id
        event     = msg.get("Event", "")         # 事件类型（如subscribe）

        # Step6：只处理主人发来的消息（安全过滤！）
        admin_user = config.get("admin_user", "")
        if user_id != admin_user:
            print(f"[回调] ⚠️ 非主人消息，已忽略：{user_id}")
            return make_response("success", 200)

        # Step7：处理不同消息类型
        if msg_type == "text":
            # 普通文字消息，放到子线程处理
            thread = threading.Thread(
                target=_process_message,
                args=(user_id, "text", content)
            )
            thread.daemon = True
            thread.start()

        elif msg_type == "image":
            # 图片消息，传入media_id，子线程里会去下载
            thread = threading.Thread(
                target=_process_message,
                args=(user_id, "image", "", media_id)
            )
            thread.daemon = True
            thread.start()

        elif msg_type == "event":
            # 订阅事件（主人第一次关注应用）
            if event.lower() == "subscribe":
                send_message(user_id,
                    "🦞 主人好！阿离已就位！\n"
                    "发 /help 可以查看所有指令哦～"
                )

        else:
            # 其他类型暂不支持（语音、视频等）
            send_message(user_id,
                f"⚠️ 阿离暂时还不支持「{msg_type}」类型的消息哦，"
                f"请发文字或图片～"
            )

        # Step8：立刻返回 success，真正的回复通过主动发消息完成
        # （这样就不会超过企业微信5秒超时限制！）
        return make_response("success", 200)