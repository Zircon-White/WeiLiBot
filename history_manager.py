# history_manager.py
"""
历史消息管理器
负责存储和读取聊天记录，供填表员使用
消息结构：
{
    "role": "user" / "assistant",
    "content": "消息内容",
    "timestamp": "2025-08-01 15:00",
    "type": "chat" / "confirm" / "reminder"  # 消息类型，思维链不存
}
"""

import json
import os
from datetime import datetime

HISTORY_FILE = "data/history.json"
MAX_HISTORY = 100  # 最多保留100条，防止文件无限膨胀


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        _save_history([])
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_history(history: list[dict]):
    os.makedirs("data", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_message(role: str, content: str, msg_type: str = "chat"):
    """
    追加一条消息到历史记录
    role: "user" 或 "assistant"
    msg_type: "chat"(普通对话) / "confirm"(归档确认) / "reminder"(提醒)
    注意：思维链内容不要传进来！
    """
    history = _load_history()
    history.append({
        "role": role,
        "content": content,
        "timestamp": _now_str(),
        "type": msg_type
    })
    # 超出上限就删掉最老的
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    _save_history(history)


def get_recent_messages(n: int) -> list[dict]:
    """
    获取最近N条消息
    返回格式适配豆包API的messages列表（只含role和content）
    """
    history = _load_history()
    recent = history[-n:] if len(history) >= n else history
    # 只返回role和content，其他字段是本地用的
    return [{"role": msg["role"], "content": msg["content"]} for msg in recent]


def clear_history():
    """清空历史记录（慎用）"""
    _save_history([])