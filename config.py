# config.py
import json
import os

CONFIG_FILE = "data/config.json"

DEFAULT_CONFIG = {
    # ── 原有配置 ──
    "history_n": 6,
    "reminder_advance": 2,

    # ── C步骤新增：企业微信配置 ──
    "corp_id": "你的企业ID",               # 企业ID
    "corp_secret": "你的应用Secret",        # 应用Secret
    "agent_id": 0,                          # 应用AgentId（数字）
    "admin_user": "你的企业微信userid",     # 主人的userid
    "wechat_token": "你设置的Token",        # 接收消息Token
    "wechat_aes_key": "你的EncodingAESKey", # 消息加解密Key
    "context_days": 7    # 默认注入未来7天，/days 指令可以修改
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def update_config(key: str, value):
    config = load_config()
    config[key] = value
    save_config(config)
