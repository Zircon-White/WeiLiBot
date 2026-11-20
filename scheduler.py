# scheduler.py
"""
定时任务模块（最终版）
接入真实的企业微信发送
"""

import schedule
import time
from config import load_config
from schedule_manager import get_upcoming_schedules
from ai_handler import handle_reminder
from wechat_handler import send_message_to_admin   # ✅ 接入真实发送

_reminded_ids: set = set()


def check_and_remind():
    """每分钟执行一次，检查即将开始的日程"""
    config = load_config()
    advance = config.get("reminder_advance", 2)

    upcoming = get_upcoming_schedules(minutes=advance)

    for item in upcoming:
        sid = item["id"]
        if sid in _reminded_ids:
            continue

        print(f"[提醒] 触发：{item['title']} @ {item['time_start']}")

        # 生成提醒消息（含三重重试和兜底）
        reminder_msg = handle_reminder(item)

        # ✅ 真实发送给主人
        success = send_message_to_admin(reminder_msg)

        if success:
            _reminded_ids.add(sid)
            print(f"[提醒] ✅ 已发送：{item['title']}")
        else:
            print(f"[提醒] ❌ 发送失败，下次重试：{item['title']}")
            # 发送失败就不加入已提醒集合，下次定时任务会重试


if __name__ == "__main__":
    print("[定时任务] 独立模式启动...")
    schedule.every(1).minutes.do(check_and_remind)
    check_and_remind()
    while True:
        schedule.run_pending()
        time.sleep(30)