# schedule_manager.py
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

SCHEDULE_FILE = "data/schedules.json"

# ─────────────────────────────────────────
# 📌 JSON结构说明（一条日程长这样）：
# {
#   "id": "唯一ID（uuid4）",
#   "title": "核心事项名称",
#   "time_start": "2025-08-01 20:00",   # ISO格式，精确到分
#   "time_end": "2025-08-01 22:00",     # 可为null（单次事件不需要结束时间）
#   "detail": "复杂备注/大模型规划细节",  # 可为null
#   "tags": ["工作", "会议"],             # 标签，可为空列表
#   "status": "pending",                 # pending / done / deleted
#   "created_at": "2025-07-30 14:23",   # 创建时间
#   "updated_at": "2025-07-30 14:23"    # 最后修改时间
# }
# ─────────────────────────────────────────

def _load_all() -> list[dict]:
    """读取全部日程（包含已删除的，内部使用）"""
    if not os.path.exists(SCHEDULE_FILE):
        _save_all([])
        return []
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_all(schedules: list[dict]):
    """保存全部日程"""
    os.makedirs("data", exist_ok=True)
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

# ════════════════════════════════════════
#  ✅ 对外接口
# ════════════════════════════════════════

def add_schedule(
    title: str,
    time_start: str,
    time_end: Optional[str] = None,
    detail: Optional[str] = None,
    tags: Optional[list] = None
) -> dict:
    """新增一条日程，返回新增的条目"""
    schedules = _load_all()
    new_item = {
        "id": str(uuid.uuid4())[:8],   # 取前8位，够用了
        "title": title,
        "time_start": time_start,
        "time_end": time_end,
        "detail": detail,
        "tags": tags or [],
        "status": "pending",
        "created_at": _now_str(),
        "updated_at": _now_str()
    }
    schedules.append(new_item)
    _save_all(schedules)
    return new_item

def update_schedule(schedule_id: str, updates: dict) -> Optional[dict]:
    """
    修改一条日程，传入id和要改的字段字典
    比如 update_schedule("abc12345", {"title": "新名字", "time_start": "..."})
    返回修改后的条目，找不到id则返回None
    """
    schedules = _load_all()
    for item in schedules:
        if item["id"] == schedule_id:
            # 只允许修改这些字段，防止乱写
            allowed_keys = {"title", "time_start", "time_end", "detail", "tags", "status"}
            for k, v in updates.items():
                if k in allowed_keys:
                    item[k] = v
            item["updated_at"] = _now_str()
            _save_all(schedules)
            return item
    return None

def delete_schedule(schedule_id: str) -> Optional[dict]:
    """
    软删除（status改为deleted），保留记录
    返回被删除的条目，找不到则返回None
    """
    return update_schedule(schedule_id, {"status": "deleted"})

def get_today_schedules() -> list[dict]:
    """获取今天所有pending的日程"""
    today = datetime.now().strftime("%Y-%m-%d")
    schedules = _load_all()
    return [
        s for s in schedules
        if s["status"] == "pending"
        and s.get("type", "single") != "repeat"   
        and s.get("time_start", "").startswith(today)
    ]

def get_all_pending_schedules() -> list[dict]:
    """获取所有pending状态的日程（按时间排序）"""
    schedules = _load_all()
    pending = [s for s in schedules
        if s["status"] == "pending"
        and s.get("type", "single") != "repeat"]
    # 按开始时间排序
    pending.sort(key=lambda x: x["time_start"])
    return pending

def get_upcoming_schedules(minutes: int = 2) -> list[dict]:
    """
    获取即将在N分钟内开始的pending日程
    供定时任务调用（scheduler.py 里会用到）
    """
    now = datetime.now()
    target = now + timedelta(minutes=minutes)
    schedules = _load_all()
    result = []
    for s in schedules:
        if s.get("type", "single") == "repeat":   # ✅ 跳过父规则
            continue
        if s["status"] != "pending":
            continue
        try:
            t = datetime.strptime(s["time_start"], "%Y-%m-%d %H:%M")
            # 在 now ~ target 这个窗口内的都要提醒
            if now <= t <= target:
                result.append(s)
        except  (ValueError, KeyError):
            continue  # 时间格式异常就跳过，不崩溃
    return result

def apply_ai_changes(changes: dict) -> dict:
    """
    接收填表员AI输出的变更指令，统一执行
    changes结构：
    {
        "add": [...],      # 新增列表
        "update": [...],   # 修改列表，每条必须有id字段
        "delete": [...]    # 删除列表，每条必须有id字段
    }
    返回执行报告
    """
    report = {
        "added": [],
        "updated": [],
        "deleted": [],
        "errors": []
    }

    # ── 新增 ──
    for item in changes.get("add", []):
        try:
            new = add_schedule(
                title=item["title"],
                time_start=item["time_start"],
                time_end=item.get("time_end"),
                detail=item.get("detail"),
                tags=item.get("tags", [])
            )
            report["added"].append(new)
        except KeyError as e:
            report["errors"].append(f"新增失败，缺少字段：{e}，原始数据：{item}")

    # ── 修改 ──
    for item in changes.get("update", []):
        try:
            sid = item["id"]
            updates = {k: v for k, v in item.items() if k != "id"}
            result = update_schedule(sid, updates)
            if result:
                report["updated"].append(result)
            else:
                report["errors"].append(f"修改失败，找不到id：{sid}")
        except KeyError as e:
            report["errors"].append(f"修改失败，缺少字段：{e}，原始数据：{item}")

    # ── 删除 ──
    for item in changes.get("delete", []):
        try:
            sid = item["id"]
            result = delete_schedule(sid)
            if result:
                report["deleted"].append(result)
            else:
                report["errors"].append(f"删除失败，找不到id：{sid}")
        except KeyError as e:
            report["errors"].append(f"删除失败，缺少字段：{e}，原始数据：{item}")

    return report

def get_schedules_in_range(days: int = 7) -> list[dict]:
    """
    获取从现在起未来N天内的所有pending日程
    供阿离对话时注入上下文用

    days=7  → 未来一周
    days=30 → 未来一个月
    days=0  → 只看今天
    """
    now = datetime.now()
    end = now + timedelta(days=days)

    schedules = _load_all()
    result = []
    for s in schedules:
        if s["status"] != "pending":
            continue
        try:
            t = datetime.strptime(s["time_start"], "%Y-%m-%d %H:%M")
            if now <= t <= end:
                result.append(s)
        except ValueError:
            continue

    # 按时间排序
    result.sort(key=lambda x: x["time_start"])
    return result

def format_schedules_for_prompt(schedules: list[dict]) -> str:
    """
    把日程列表格式化成适合塞进Prompt的紧凑文字，节省Token！
    """
    if not schedules:
        return "（暂无已安排的日程）"

    lines = []
    current_date = ""

    for s in schedules:
        if s.get("type", "single") == "repeat":  # ✅ 双重保险
            continue
        if not s.get("time_start"):              # ✅ 没有time_start直接跳过
            continue
        # 按日期分组，相同日期只显示一次日期头
        date = s["time_start"][:10]
        if date != current_date:
            current_date = date
            # 加上星期几
            weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            weekday = weekday_map[datetime.strptime(date, "%Y-%m-%d").weekday()]
            lines.append(f"\n【{date} {weekday}】")

        # 时间段
        time_str = s["time_start"][11:]
        if s.get("time_end"):
            time_str += f"~{s['time_end'][11:]}"

        # 核心信息（备注太长就截断，节省Token）
        detail = ""
        if s.get("detail"):
            detail_text = s["detail"]
            detail = f" ※{detail_text[:20]}{'...' if len(detail_text) > 20 else ''}"

        # 标签
        tags = f" [{'/'.join(s['tags'])}]" if s.get("tags") else ""

        lines.append(f"  · {time_str} {s['title']}{tags}{detail}  🔑{s['id']}")

    return "\n".join(lines)

# ════════════════════════════════════════
#  重复日程：新增
# ════════════════════════════════════════

def add_repeat_schedule(
    title: str,
    date_start: str,
    date_end: str,
    interval_hours: float,
    sleep_start: str = "23:00",
    sleep_end:   str = "08:00",
    duration_minutes: int = 0,
    detail: str = None,
    tags: list = None
) -> dict:
    """
    新增一条重复日程规则
    date_start / date_end：YYYY-MM-DD
    interval_hours：间隔小时数（支持小数，如1.5表示每90分钟）
    sleep_start / sleep_end：睡眠时段，这段时间内不生成提醒点
    duration_minutes：每次提醒事项的持续时长（分钟），0表示纯提醒无结束时间
    """
    schedules = _load_all()
    new_item = {
        "id":               str(uuid.uuid4())[:8],
        "type":             "repeat",
        "title":            title,
        "date_start":       date_start,
        "date_end":         date_end,
        "interval_hours":   interval_hours,
        "sleep_start":      sleep_start,
        "sleep_end":        sleep_end,
        "duration_minutes": duration_minutes,
        "detail":           detail,
        "tags":             tags or [],
        "status":           "pending",
        "created_at":       _now_str(),
        "updated_at":       _now_str()
    }
    schedules.append(new_item)
    _save_all(schedules)
    return new_item


# ════════════════════════════════════════
#  重复日程：展开成时间点列表
# ════════════════════════════════════════

def expand_repeat_schedule(
    rule: dict,
    existing_schedules: list[dict] = None
) -> list[dict]:
    """
    把一条重复日程规则展开成具体的时间点列表
    同时自动跳过：
      · 睡眠时段
      · 和已有单条日程冲突的时间段

    返回展开后的时间点列表（虚拟的，不写入文件）
    每个元素格式和普通日程一致，id用 原id_序号 表示
    """

    if existing_schedules is None:
        existing_schedules = [
            s for s in _load_all()
            if s.get("type", "single") == "single"
            and s["status"] == "pending"
        ]

    # ── 解析规则参数 ──
    date_start      = datetime.strptime(rule["date_start"], "%Y-%m-%d")
    date_end        = datetime.strptime(rule["date_end"],   "%Y-%m-%d")
    interval        = timedelta(hours=rule["interval_hours"])
    duration_mins   = rule.get("duration_minutes", 0)
    sleep_start_str = rule.get("sleep_start", "23:00")
    sleep_end_str   = rule.get("sleep_end",   "08:00")

    # ── 解析睡眠时间（只取时分）──
    sleep_start_t = datetime.strptime(sleep_start_str, "%H:%M").time()
    sleep_end_t   = datetime.strptime(sleep_end_str,   "%H:%M").time()

    # ── 生成候选 ```python
    # ── 生成候选时间点 ──
    results = []
    seq = 0

    # 从 date_start 的 sleep_end 时间开始（即第一天睡醒后开始）
    current = datetime.combine(date_start.date(), sleep_end_t)
    end_boundary = datetime.combine(date_end.date(), sleep_start_t) + timedelta(days=1)

    while current <= end_boundary:

        # ── 检查1：是否在睡眠时段内 ──
        t = current.time()
        in_sleep = False

        if sleep_start_t > sleep_end_t:
            # 跨午夜的情况（比如23:00~08:00）
            if t >= sleep_start_t or t < sleep_end_t:
                in_sleep = True
        else:
            # 不跨午夜（比如14:00~16:00的午休）
            if sleep_start_t <= t < sleep_end_t:
                in_sleep = True

        if in_sleep:
            current += interval
            continue

        # ── 检查2：是否超出日期范围 ──
        if current.date() > date_end.date():
            break

        # ── 检查3：是否和已有单条日程冲突 ──
        point_end = current + timedelta(minutes=duration_mins) if duration_mins > 0 else current + timedelta(minutes=1)

        conflict = _check_conflict(current, point_end, existing_schedules)

        if not conflict:
            # ✅ 这个时间点可用，加入结果
            seq += 1
            item = {
                "id":         f"{rule['id']}_{seq:03d}",
                "type":       "repeat_instance",    # 标记为展开实例
                "repeat_id":  rule["id"],           # 关联到父规则
                "title":      rule["title"],
                "time_start": current.strftime("%Y-%m-%d %H:%M"),
                "time_end":   (current + timedelta(minutes=duration_mins)).strftime("%Y-%m-%d %H:%M") if duration_mins > 0 else None,
                "detail":     rule.get("detail"),
                "tags":       rule.get("tags", []),
                "status":     "pending"
            }
            results.append(item)
        else:
            # 有冲突，记录一下方便预览时告知主人
            seq += 1
            item = {
                "id":           f"{rule['id']}_{seq:03d}",
                "type":         "repeat_instance",
                "repeat_id":    rule["id"],
                "title":        rule["title"],
                "time_start":   current.strftime("%Y-%m-%d %H:%M"),
                "time_end":     None,
                "status":       "skipped",          # 被冲突跳过
                "skip_reason":  f"与「{conflict['title']}」冲突"
            }
            results.append(item)

        current += interval

    return results


def _check_conflict(
    start: datetime,
    end: datetime,
    existing: list[dict]
) -> dict | None:
    """
    检查 start~end 这个时间段是否和已有日程冲突
    返回冲突的日程条目，没冲突返回None
    """
    for s in existing:
        try:
            s_start = datetime.strptime(s["time_start"], "%Y-%m-%d %H:%M")
            s_end   = datetime.strptime(s["time_end"],   "%Y-%m-%d %H:%M") if s.get("time_end") else s_start + timedelta(minutes=30)

            # 两个区间有重叠的条件
            if start < s_end and end > s_start:
                return s
        except (ValueError, KeyError):
            continue
    return None


# ════════════════════════════════════════
#  重复日程：预览（不写入，只展示结果）
# ════════════════════════════════════════

def preview_repeat_schedule(rule: dict) -> str:
    """
    展开并格式化预览文字，供阿离发给主人确认用
    """
    instances = expand_repeat_schedule(rule)

    available = [i for i in instances if i["status"] == "pending"]
    skipped   = [i for i in instances if i["status"] == "skipped"]

    lines = [
        f"📋 重复日程预览：「{rule['title']}」",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📆 周期：{rule['date_start']} ~ {rule['date_end']}",
        f"⏱️ 间隔：每 {rule['interval_hours']} 小时",
        f"😴 跳过：{rule['sleep_start']} ~ {rule['sleep_end']}（睡眠时段）",
        f"",
        f"✅ 可用时间点（共 {len(available)} 个）："
    ]

    # 按日期分组展示
    groups = {}
    for inst in available:
        date = inst["time_start"][:10]
        groups.setdefault(date, []).append(inst)

    for date, day_items in groups.items():
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_map[datetime.strptime(date, "%Y-%m-%d").weekday()]
        lines.append(f"\n  📅 {date}（{weekday}）")
        for inst in day_items:
            t = inst["time_start"][11:]
            if inst.get("time_end"):
                t += f"~{inst['time_end'][11:]}"
            lines.append(f"    · {t} ✅")

    if skipped:
        lines.append(f"\n⚠️ 已跳过（共 {len(skipped)} 个，因冲突）：")
        for inst in skipped:
            t   = inst["time_start"][11:]
            reason = inst.get("skip_reason", "未知原因")
            lines.append(f"    · {t} ❌ {reason}")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"回复 y → 确认归档全部 {len(available)} 个提醒点")
    lines.append(f"回复 /edit cancel → 取消本次操作")

    return "\n".join(lines)


# ════════════════════════════════════════
#  重复日程：确认后写入
# ════════════════════════════════════════

def commit_repeat_schedule(rule: dict) -> dict:
    """
    把重复日程规则和展开的实例全部写入schedules.json
    返回写入报告
    """
    instances = expand_repeat_schedule(rule)
    available = [i for i in instances if i["status"] == "pending"]
    skipped   = [i for i in instances if i["status"] == "skipped"]

    schedules = _load_all()

    # 写入父规则
    schedules.append(rule)

    # 写入所有可用实例
    for inst in available:
        schedules.append(inst)

    _save_all(schedules)

    return {
        "rule":      rule,
        "committed": len(available),
        "skipped":   len(skipped)
    }


# ════════════════════════════════════════
#  删除整个重复日程系列
# ════════════════════════════════════════

def delete_repeat_series(repeat_id: str) -> dict:
    """
    软删除一个重复日程的父规则和所有子实例
    返回删除报告
    """
    schedules = _load_all()
    deleted_count = 0

    for s in schedules:
        # 删父规则
        if s["id"] == repeat_id and s.get("type") == "repeat":
            s["status"] = "deleted"
            s["updated_at"] = _now_str()
            deleted_count += 1
        # 删所有子实例
        elif s.get("repeat_id") == repeat_id and s["status"] == "pending":
            s["status"] = "deleted"
            s["updated_at"] = _now_str()
            deleted_count += 1

    _save_all(schedules)
    return {"repeat_id": repeat_id, "deleted_count": deleted_count}


# ════════════════════════════════════════
#  更新 get_upcoming_schedules，让提醒系统
#  能扫描到 repeat_instance 类型的条目
# ════════════════════════════════════════

def get_upcoming_schedules(minutes: int = 2) -> list[dict]:
    """
    获取即将在N分钟内开始的pending日程
    ✅ 现在同时扫描 single 和 repeat_instance 两种类型
    """
    now = datetime.now()
    target = now + timedelta(minutes=minutes)
    schedules = _load_all()
    result = []

    for s in schedules:
        # 只扫描 single 和 repeat_instance，跳过父规则
        if s.get("type") == "repeat":
            continue
        if s["status"] != "pending":
            continue
        try:
            t = datetime.strptime(s["time_start"], "%Y-%m-%d %H:%M")
            if now <= t <= target:
                result.append(s)
        except ValueError:
            continue

    return result


# ════════════════════════════════════════
#  更新 get_schedules_in_range，注入上下文时
#  也把 repeat_instance 展示给阿离看
# ════════════════════════════════════════

def get_schedules_in_range(days: int = 7) -> list[dict]:
    """
    获取从现在起未来N天内的所有pending日程
    ✅ 同时包含 single 和 repeat_instance，排除父规则
    """
    now = datetime.now()
    end = now + timedelta(days=days)

    schedules = _load_all()
    result = []

    for s in schedules:
        if s.get("type", "single") == "repeat":      # 父规则不展示
            continue
        if s["status"] != "pending":
            continue
        try:
            t = datetime.strptime(s["time_start"], "%Y-%m-%d %H:%M")
            if now <= t <= end:
                result.append(s)
        except  (ValueError, KeyError):
            continue

    result.sort(key=lambda x: x["time_start"])
    return result