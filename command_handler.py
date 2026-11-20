# command_handler.py
import json
from schedule_manager import (
    get_today_schedules,
    get_all_pending_schedules,
    delete_schedule,
    update_schedule,
    _load_all,
    delete_repeat_series
)
from config import load_config, update_config
from datetime import datetime

# ════════════════════════════════════════
#  等待二次确认的状态缓存（内存级）
#  格式：
#  {
#    "pending_delete": "a1b2c3d4",      # 等待确认删除的ID
#    "pending_edit":   "a1b2c3d4",      # 等待输入修改内容的ID
#  }
# ════════════════════════════════════════
_pending_state: dict = {}


# ════════════════════════════════════════
#  帮助文档（更新）
# ════════════════════════════════════════
HELP_TEXT = """
🦞 阿离快捷指令手册 🦞
━━━━━━━━━━━━━━━━━━━━
📋 日程查看
  /today          查看今天的所有日程
  /all            查看全部待办日程
  /series         查看所有重复日程系列    

✏️ 日程操作
  /edit  [ID]     修改指定日程
  /del   [ID]     删除指定单条日程
  /dels  [ID]     删除整个重复系列         

⚙️ 参数调整
  /n    [数字]    设置填表员读取的历史消息数
  /days [数字]    设置阿离感知几天内的已有日程

📖 其他
  /help           显示本帮助菜单
  /config         查看当前所有配置参数
━━━━━━━━━━━━━━━━━━━━
💡 ID可在 /today 或 /all 结果里找到 🔑
💡 重复系列ID可在 /series 里找到 🔑
""".strip()


# ════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════

def _find_schedule_by_id(schedule_id: str) -> dict | None:
    """根据ID查找单条日程（不限status）"""
    all_schedules = _load_all()
    for s in all_schedules:
        if s["id"] == schedule_id:
            return s
    return None


def _format_single_schedule(s: dict) -> str:
    """把单条日程格式化成易读的文字"""
    lines = []
    lines.append(f"  🔑 ID     ：{s['id']}")
    lines.append(f"  📌 标题   ：{s['title']}")
    lines.append(f"  ⏰ 开始时间：{s['time_start']}")

    if s.get("time_end"):
        lines.append(f"  ⏰ 结束时间：{s['time_end']}")
    else:
        lines.append(f"  ⏰ 结束时间：（未设置）")

    if s.get("detail"):
        lines.append(f"  📝 备注    ：{s['detail']}")
    else:
        lines.append(f"  📝 备注    ：（无）")

    if s.get("tags"):
        lines.append(f"  🏷️ 标签    ：{' '.join(s['tags'])}")
    else:
        lines.append(f"  🏷️ 标签    ：（无）")

    return "\n".join(lines)


def _parse_edit_input(raw: str) -> tuple[bool, dict, str]:
    """
    解析主人输入的修改内容
    支持格式：
      title=新标题
      time=2025-08-03 16:00
      end=2025-08-03 17:00
      detail=新备注
      tags=工作,会议
      done                     ← 标记为完成

    多个字段空行分隔或换行均可：
      title=复习高数
      time=2025-08-03 14:00
      detail=重点看第三章

    返回：
      (True,  updates字典,  "")       → 解析成功
      (False, {},           "错误描述") → 解析失败
    """
    updates = {}
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]

    # 特殊情况：直接输入 done 标记完成
    if len(lines) == 1 and lines[0].lower() == "done":
        return True, {"status": "done"}, ""

    for line in lines:
        # 必须含有 = 号
        if "=" not in line:
            return False, {}, f"「{line}」格式不对，请用 字段=值 的格式哦～"

        key, _, value = line.partition("=")
        key   = key.strip().lower()
        value = value.strip()

        if not value:
            return False, {}, f"「{key}」的值不能为空哦！"

        # ── 字段映射 ──
        if key == "title":
            updates["title"] = value

        elif key == "time":
            # 校验时间格式
            from datetime import datetime
            try:
                datetime.strptime(value, "%Y-%m-%d %H:%M")
                updates["time_start"] = value
            except ValueError:
                return False, {}, f"时间格式不对哦！请用 YYYY-MM-DD HH:MM 格式\n例：time=2025-08-03 16:00"

        elif key == "end":
            if value.lower() in ("null", "无", "取消"):
                updates["time_end"] = None
            else:
                from datetime import datetime
                try:
                    datetime.strptime(value, "%Y-%m-%d %H:%M")
                    updates["time_end"] = value
                except ValueError:
                    return False, {}, f"结束时间格式不对哦！请用 YYYY-MM-DD HH:MM 格式\n例：end=2025-08-03 17:00"

        elif key == "detail":
            updates["detail"] = None if value.lower() in ("null", "无", "取消") else value

        elif key == "tags":
            # 支持逗号或空格分隔
            tag_list = [t.strip() for t in value.replace("，", ",").split(",") if t.strip()]
            updates["tags"] = tag_list

        elif key == "status":
            if value.lower() not in ("pending", "done"):
                return False, {}, f"status 只能填 pending 或 done 哦！"
            updates["status"] = value.lower()

        else:
            return False, {}, (
                f"阿离不认识「{key}」这个字段哦！\n"
                f"可用的字段：title / time / end / detail / tags / status"
            )

    if not updates:
        return False, {}, "没有检测到任何修改内容哦，请至少填一个字段～"

    return True, updates, ""


# ════════════════════════════════════════
#  /del 指令处理
# ════════════════════════════════════════

def _handle_del(args: str) -> str:
    """
    /del [ID] 第一步：展示要删除的日程，等待确认
    """
    schedule_id = args.strip()

    if not schedule_id:
        return (
            "⚠️ 请告诉阿离要删除哪条日程的ID哦！\n"
            "用法：/del [ID]\n"
            "例：/del a1b2c3d4\n\n"
            "💡 发送 /today 或 /all 可以查看日程ID 🔑"
        )

    # 查找日程
    s = _find_schedule_by_id(schedule_id)

    if not s:
        return f"❓ 找不到 ID 为「{schedule_id}」的日程哦，请检查ID是否正确～"

    if s["status"] == "deleted":
        return f"⚠️ 这条日程已经被删除过了哦！"

    # 缓存等待确认的状态
    _pending_state["pending_delete"] = schedule_id
    # 清掉可能存在的edit状态，防止串台
    _pending_state.pop("pending_edit", None)

    return (
        f"🗑️ 确认删除以下日程？\n"
        f"────────────────\n"
        f"{_format_single_schedule(s)}\n"
        f"────────────────\n"
        f"✅ 确认删除 → 回复 /del confirm\n"
        f"❌ 取消操作 → 回复 /del cancel\n"
    )


def _handle_del_confirm() -> str:
    """
    /del confirm 第二步：真正执行删除
    """
    schedule_id = _pending_state.get("pending_delete")

    if not schedule_id:
        return "⚠️ 没有等待确认的删除操作哦，请先发送 /del [ID]～"

    result = delete_schedule(schedule_id)
    _pending_state.pop("pending_delete", None)

    if result:
        return (
            f"✅ 已删除！\n"
            f"────────────────\n"
            f"  📌 {result['title']}\n"
            f"  ⏰ {result['time_start']}\n"
            f"────────────────\n"
            f"（软删除，数据仍保留在文件中，status已标记为deleted）"
        )
    else:
        return "❌ 删除失败，日程可能已不存在，请发送 /all 检查一下～"


def _handle_del_cancel() -> str:
    """
    /del cancel 取消删除
    """
    had_pending = "pending_delete" in _pending_state
    _pending_state.pop("pending_delete", None)

    if had_pending:
        return "👌 已取消删除操作，日程安全！🦞"
    else:
        return "⚠️ 当前没有等待确认的删除操作哦～"


# ════════════════════════════════════════
#  /edit 指令处理
# ════════════════════════════════════════

def _handle_edit(args: str) -> str:
    """
    /edit [ID] 第一步：展示当前内容，告知可修改字段
    """
    schedule_id = args.strip()

    if not schedule_id:
        return (
            "⚠️ 请告诉阿离要修改哪条日程的ID哦！\n"
            "用法：/edit [ID]\n"
            "例：/edit a1b2c3d4\n\n"
            "💡 发送 /today 或 /all 可以查看日程ID 🔑"
        )

    # 查找日程
    s = _find_schedule_by_id(schedule_id)

    if not s:
        return f"❓ 找不到 ID 为「{schedule_id}」的日程哦，请检查ID是否正确～"

    if s["status"] == "deleted":
        return f"⚠️ 这条日程已经被删除了，没办法修改哦！"

    # 缓存等待编辑的状态
    _pending_state["pending_edit"] = schedule_id
    # 清掉可能存在的delete状态，防止串台
    _pending_state.pop("pending_delete", None)

    return (
        f"✏️ 正在编辑以下日程：\n"
        f"────────────────\n"
        f"{_format_single_schedule(s)}\n"
        f"────────────────\n"
        f"📝 请发送要修改的字段（可同时修改多个，每行一个）：\n\n"
        f"  title=新标题\n"
        f"  time=2025-08-03 16:00\n"
        f"  end=2025-08-03 17:00\n"
        f"  detail=新备注\n"
        f"  tags=标签1,标签2\n"
        f"  status=done\n\n"
        f"💡 不需要修改的字段直接不填就行\n"
        f"💡 发送 /edit cancel 可以取消本次编 辑"
    )


def _handle_edit_cancel() -> str:
    """
    /edit cancel 取消编辑
    """
    had_pending = "pending_edit" in _pending_state
    _pending_state.pop("pending_edit", None)

    if had_pending:
        return "👌 已取消编辑操作，日程没有任何改动！🦞"
    else:
        return "⚠️ 当前没有等待编辑的操作哦～"


def _handle_edit_input(raw_input: str) -> str:
    """
    /edit 第二步：接收主人输入的修改内容并执行
    （这个函数不是由指令触发的，而是由 try_handle_command 里的状态机触发）
    """
    schedule_id = _pending_state.get("pending_edit")

    if not schedule_id:
        return ""  # 不是edit状态，返回空让后续流程处理

    # 解析输入
    ok, updates, err_msg = _parse_edit_input(raw_input)

    if not ok:
        return (
            f"⚠️ 输入有点问题：{err_msg}\n\n"
            f"请重新发送修改内容，或发送 /edit cancel 取消～"
        )

    # 执行修改
    result = update_schedule(schedule_id, updates)
    _pending_state.pop("pending_edit", None)

    if not result:
        return "❌ 修改失败，日程可能已不存在，请发送 /all 检查一下～"

    # 格式化修改了哪些字段的说明
    field_name_map = {
        "title":      "标题",
        "time_start": "开始时间",
        "time_end":   "结束时间",
        "detail":     "备注",
        "tags":       "标签",
        "status":     "状态"
    }
    changed_fields = "、".join(
        field_name_map.get(k, k) for k in updates.keys()
    )

    return (
        f"✅ 修改成功！\n"
        f"────────────────\n"
        f"{_format_single_schedule(result)}\n"
        f"────────────────\n"
        f"📝 已更新字段：{changed_fields}"
    )


# ════════════════════════════════════════
#  原有处理函数（保持不变）
# ════════════════════════════════════════

def _handle_today() -> str:
    items = get_today_schedules()
    if not items:
        return "🦞 今天没有待办日程哦，主人可以好好休息！"

    lines = ["📅 今日日程 ──────────────"]
    for i, s in enumerate(items, 1):
        time_str = s["time_start"][11:]
        if s.get("time_end"):
            time_str += f" ~ {s['time_end'][11:]}"
        line = f"{i}. [{time_str}] {s['title']}"
        if s.get("detail"):
            line += f"\n   📝 {s['detail']}"
        if s.get("tags"):
            line += f"\n   🏷️ {' '.join(s['tags'])}"
        line += f"\n   🔑 ID: {s['id']}"
        lines.append(line)

    lines.append(f"────────────────\n共 {len(items)} 条")
    return "\n".join(lines)


def _handle_all() -> str:
    items = get_all_pending_schedules()
    if not items:
        return "🦞 日程表空空如也，主人还没有添加任何事项哦！"

    groups = {}
    for s in items:
        date = s["time_start"][:10]
        groups.setdefault(date, []).append(s)

    lines = ["🗂️ 全部待办日程 ──────────────"]
    for date, day_items in groups.items():
        lines.append(f"\n📆 {date}")
        for s in day_items:
            time_str = s["time_start"][11:]
            if s.get("time_end"):
                time_str += f" ~ {s['time_end'][11:]}"
            line = f"  · [{time_str}] {s['title']}  🔑{s['id']}"
            lines.append(line)

    lines.append(f"\n────────────────\n共 {len(items)} 条待办")
    return "\n".join(lines)


def _handle_set_n(args: str) -> str:
    try:
        n = int(args.strip())
        if n < 2:
            return "⚠️ 消息数至少要设置为 2 哦！"
        if n > 30:
            return "⚠️ 消息数最多设置 30 哦！"
        update_config("history_n", n)
        return f"✅ 已更新！填表员现在会读取最近 {n} 条消息啦，咔嚓！🦞"
    except ValueError:
        return "⚠️ 格式不对哦！正确用法：/n 6"


def _handle_set_days(args: str) -> str:
    try:
        d = int(args.strip())
        if d < 1:
            return "⚠️ 天数至少设置为 1 哦！"
        if d > 90:
            return "⚠️ 天数最多设置 90 天哦！"
        update_config("context_days", d)
        return f"✅ 已更新！阿离现在会感知未来 {d} 天内的日程啦！🦞"
    except ValueError:
        return "⚠️ 格式不对哦！正确用法：/days 14"


def _handle_config() -> str:
    config = load_config()
    lines = ["⚙️ 当前配置参数 ──────────────"]
    lines.append(f"  📨 填表员历史消息数  (history_n)    ：{config.get('history_n', 6)}")
    lines.append(f"  📅 日程感知天数      (context_days) ：{config.get('context_days', 7)} 天")
    lines.append(f"  ⏰ 提前提醒时间      (reminder_advance)：{config.get('reminder_advance', 2)} 分钟")
    lines.append(f"  👤 管理员ID          (admin_user)   ：{config.get('admin_user', '未设置')}")
    lines.append("────────────────")
    lines.append("💡 /n [数字]    修改历史消息数")
    lines.append("💡 /days [数字] 修改日程感知天数")
    return "\n".join(lines)


# ════════════════════════════════════════
#  🌟 对外统一入口（完整版，含状态机）
# ════════════════════════════════════════

def try_handle_command(message: str) -> tuple[bool, str]:
    msg = message.strip()

    # ── 状态机检查（优先级最高）──
    if "pending_edit" in _pending_state:
        if msg.lower() not in ("/edit cancel", "/del cancel", "/dels cancel"):
            reply = _handle_edit_input(msg)
            if reply:
                return True, reply

    if not msg.startswith("/"):
        return False, ""

    parts = msg.split(maxsplit=1)
    cmd  = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        return True, HELP_TEXT
    elif cmd == "/today":
        return True, _handle_today()
    elif cmd == "/all":
        return True, _handle_all()
    elif cmd == "/series":
        return True, _handle_series()
    elif cmd == "/n":
        return True, _handle_set_n(args)
    elif cmd == "/days":
        return True, _handle_set_days(args)
    elif cmd == "/config":
        return True, _handle_config()

    elif cmd == "/del":
        sub = args.strip().lower()
        if sub == "confirm":  return True, _handle_del_confirm()
        elif sub == "cancel": return True, _handle_del_cancel()
        elif sub == "":       return True, "⚠️ 请提供要删除的日程ID！\n用法：/del [ID]"
        else:                 return True, _handle_del(args)

    elif cmd == "/dels":
        sub = args.strip().lower()
        if sub == "confirm":  return True, _handle_dels_confirm()
        elif sub == "cancel": return True, _handle_dels_cancel()
        elif sub == "":       return True, "⚠️ 请提供要删除的系列ID！\n用法：/dels [ID]"
        else:                 return True, _handle_dels(args)

    elif cmd == "/edit":
        sub = args.strip().lower()
        if sub == "cancel": return True, _handle_edit_cancel()
        elif sub == "":     return True, "⚠️ 请提供要修改的日程ID！\n用法：/edit [ID]"
        else:               return True, _handle_edit(args)

    else:
        return True, f"❓ 阿离不认识「{cmd}」这个指令哦！\n发送 /help 查看所有指令～ 🦞"
    
def _handle_series() -> str:
    """
    /series 查看所有重复日程系列（父规则）
    """
    all_schedules = _load_all()
    rules = [
        s for s in all_schedules
        if s.get("type") == "repeat" and s["status"] == "pending"
    ]

    if not rules:
        return "🦞 目前没有任何重复日程系列哦！"

    lines = ["🔁 重复日程系列 ──────────────"]

    for r in rules:
        # 统计该系列还剩多少个pending实例
        instances = [
            s for s in all_schedules
            if s.get("repeat_id") == r["id"] and s["status"] == "pending"
        ]
        lines.append(
            f"\n🔑 {r['id']}\n"
            f"  📌 {r['title']}\n"
            f"  📆 {r['date_start']} ~ {r['date_end']}\n"
            f"  ⏱️ 每隔 {r['interval_hours']} 小时\n"
            f"  😴 跳过 {r['sleep_start']} ~ {r['sleep_end']}\n"
            f"  📊 剩余提醒点：{len(instances)} 个"
        )

    lines.append("\n────────────────")
    lines.append("💡 /dels [ID] 可删除整个系列的所有提醒点")
    return "\n".join(lines)

def _handle_dels(args: str) -> str:
    """
    /dels [ID] 第一步：展示要删除的系列，等待确认
    """
    repeat_id = args.strip()

    if not repeat_id:
        return (
            "⚠️ 请提供要删除的重复系列ID哦！\n"
            "用法：/dels [ID]\n"
            "例：/dels r1s2t3u4\n\n"
            "💡 发送 /series 可以查看所有重复系列ID 🔑"
        )

    all_schedules = _load_all()

    # 找父规则
    rule = next(
        (s for s in all_schedules
         if s["id"] == repeat_id and s.get("type") == "repeat"),
        None
    )

    if not rule:
        return f"❓ 找不到 ID 为「{repeat_id}」的重复系列哦，请检查ID是否正确～"

    if rule["status"] == "deleted":
        return "⚠️ 这个系列已经被删除过了哦！"

    # 统计子实例数量
    instances = [
        s for s in all_schedules
        if s.get("repeat_id") == repeat_id and s["status"] == "pending"
    ]

    # 缓存等待确认的状态
    _pending_state["pending_dels"] = repeat_id
    _pending_state.pop("pending_delete", None)
    _pending_state.pop("pending_edit", None)

    return (
        f"🗑️ 确认删除以下重复日程系列？\n"
        f"────────────────\n"
        f"  🔑 ID     ：{rule['id']}\n"
        f"  📌 标题   ：{rule['title']}\n"
        f"  📆 周期   ：{rule['date_start']} ~ {rule['date_end']}\n"
        f"  ⏱️ 间隔   ：每 {rule['interval_hours']} 小时\n"
        f"  📊 将删除 ：{len(instances)} 个提醒点\n"
        f"────────────────\n"
        f"✅ 确认删除 → 回复 /dels confirm\n"
        f"❌ 取消操作 → 回复 /dels cancel\n"
    )


def _handle_dels_confirm() -> str:
    """
    /dels confirm 第二步：真正执行整个系列删除
    """
    repeat_id = _pending_state.get("pending_dels")

    if not repeat_id:
        return "⚠️ 没有等待确认的系列删除操作哦，请先发送 /dels [ID]～"

    result = delete_repeat_series(repeat_id)
    _pending_state.pop("pending_dels", None)

    return (
        f"✅ 已删除整个重复系列！\n"
        f"────────────────\n"
        f"  🔑 系列ID  ：{repeat_id}\n"
        f"  📊 共删除  ：{result['deleted_count']} 条记录\n"
        f"────────────────\n"
        f"（软删除，数据仍保留在文件中）"
    )


def _handle_dels_cancel() -> str:
    """
    /dels cancel 取消系列删除
    """
    had_pending = "pending_dels" in _pending_state
    _pending_state.pop("pending_dels", None)

    if had_pending:
        return "👌 已取消系列删除操作，所有提醒点安全！🦞"
    else:
        return "⚠️ 当前没有等待确认的系列删除操作哦～"