# run.py
"""
统一启动入口
同时启动：
  · Flask Web服务（接收企业微信回调）
  · 定时任务（扫描日程提醒）
"""

import threading
import schedule as schedule_lib
import time
from main import app
from scheduler import check_and_remind
from config import load_config


def run_scheduler():
    """在子线程里跑定时任务"""
    print("[定时任务] 启动，每分钟扫描一次日程...")
    schedule_lib.every(1).minutes.do(check_and_remind)
    # 启动时立刻跑一次
    check_and_remind()
    while True:
        schedule_lib.run_pending()
        time.sleep(30)


def run_flask():
    """启动Flask服务"""
    config = load_config()
    print("[Flask] 启动，监听 0.0.0.0:5000")
    # 生产环境建议换成 gunicorn，这里用 Flask 内置服务器
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    print("=" * 50)
    print("  🦞 阿离日程助手 启动中...")
    print("=" * 50)

    # 定时任务跑在子线程
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    # Flask 跑在主线程
    run_flask()