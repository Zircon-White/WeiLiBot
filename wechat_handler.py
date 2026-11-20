# wechat_handler.py
"""
企业微信消息收发模块
负责：
  1. 主动发消息给主人（文字）
  2. 获取企业微信AccessToken（自动缓存刷新）
  3. 处理回调验证
  4. 下载图片媒体文件
"""

import time
import requests
from config import load_config

# ════════════════════════════════════════
#  AccessToken 缓存（内存级，重启会刷新）
# ════════════════════════════════════════

_token_cache = {
    "access_token": "",
    "expires_at": 0  # 过期时间戳
}


def _get_access_token() -> str:
    """
    获取企业微信 AccessToken
    有效期7200秒，提前5分钟自动刷新
    """
    now = time.time()

    # 缓存还有效就直接返回
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]

    # 重新获取
    config = load_config()
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {
        "corpid": config["corp_id"],
        "corpsecret": config["corp_secret"]
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("errcode", 0) == 0:
            _token_cache["access_token"] = data["access_token"]
            _token_cache["expires_at"] = now + data["expires_in"]
            print(f"[Token] 刷新成功，有效至 {time.strftime('%H:%M:%S', time.localtime(_token_cache['expires_at']))}")
            return _token_cache["access_token"]
        else:
            print(f"[Token] 获取失败：{data}")
            return ""

    except requests.RequestException as e:
        print(f"[Token] 网络异常：{e}")
        return ""


# ════════════════════════════════════════
#  主动发消息给主人
# ════════════════════════════════════════

def send_message(user_id: str, content: str) -> bool:
    """
    主动发文字消息给指定用户
    user_id: 企业微信的userid
    content: 消息内容
    返回是否发送成功
    """
    token = _get_access_token()
    if not token:
        print("[发送] 获取Token失败，无法发送消息")
        return False

    config = load_config()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"

    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": config["agent_id"],
        "text": {
            "content": content
        },
        "safe": 0
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if data.get("errcode", 0) == 0:
            print(f"[发送] ✅ 消息已发送给 {user_id}")
            return True
        else:
            print(f"[发送] ❌ 发送失败：{data}")
            return False

    except requests.RequestException as e:
        print(f"[发送] 网络异常：{e}")
        return False


def send_message_to_admin(content: str) -> bool:
    """
    快捷方法：直接发给主人（从config读userid）
    scheduler.py 和其他模块都用这个
    """
    config = load_config()
    admin = config.get("admin_user", "")
    if not admin:
        print("[发送] 未配置 admin_user，无法发送！")
        return False
    return send_message(admin, content)


# ════════════════════════════════════════
#  下载图片媒体文件
# ════════════════════════════════════════

def download_media(media_id: str) -> bytes | None:
    """
    通过 media_id 下载企业微信发来的图片
    返回图片的原始字节，失败返回 None
    """
    token = _get_access_token()
    if not token:
        print("[媒体] 获取Token失败，无法下载图片")
        return None

    url = "https://qyapi.weixin.qq.com/cgi-bin/media/get"
    params = {
        "access_token": token,
        "media_id": media_id
    }

    try:
        resp = requests.get(url, params=params, timeout=15)

        # 如果返回的是JSON说明报错了
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type:
            print(f"[媒体] 下载失败：{resp.json()}")
            return None

        print(f"[媒体] ✅ 图片下载成功，大小：{len(resp.content)} bytes")
        return resp.content

    except requests.RequestException as e:
        print(f"[媒体] 网络异常：{e}")
        return None