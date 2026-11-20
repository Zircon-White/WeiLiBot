# ai_handler.py
"""
豆包API核心调用层
负责：
  1. 封装API调用（含重试逻辑）
  2. 处理三种消息模式
  3. 格式检测和鲁棒性保障
"""

import json
import time
import base64
from openai import OpenAI  # 豆包兼容OpenAI SDK！直接用

from config import load_config
from history_manager import append_message, get_recent_messages

from datetime import datetime
import uuid

from prompt_builder import (
    build_chat_messages,
    build_filler_messages,
    build_reminder_messages
)

# ════════════════════════════════════════
#  初始化豆包客户端
# ════════════════════════════════════════

# ⚠️ 主人记得把自己的Key填在这里！
ARK_API_KEY  = "44691d90-70b4-49ec-ba2f-e825ce254cba"

# 豆包的两个模型ID（主人按需填入实际的endpoint ID）
MODEL_CHAT     = "doubao-seed-1-8-251228"      # 对话用（支持多模态）
MODEL_FILLER   = "doubao-seed-code-preview-251028"    # 填表员用（纯文字，便宜一点也没关系）

client = OpenAI(
    api_key=ARK_API_KEY,
    base_url="https://ark.cn-beijing.volces.com/api/v3"  # 豆包火山引擎地址
)

MAX_RETRIES = 3          # 最大重试次数
RETRY_DELAY = 1.5        # 每次重试间隔（秒）


# ════════════════════════════════════════
#  底层：带重试的API调用
# ════════════════════════════════════════

def _call_api(messages: list[dict], model: str, expect_json: bool = False) -> tuple[bool, str]:
    """
    调用豆包API，带重试逻辑

    参数：
        messages:     构建好的消息列表
        model:        使用的模型ID
        expect_json:  是否期望返回纯JSON（填表员模式用True）

    返回：
        (True, 内容)   → 成功
        (False, 错误信息) → 全部重试失败
    """
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[API] 第 {attempt} 次请求，模型：{model}")

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3 if expect_json else 0.7,  # 填表员用低温度，更严谨
                max_tokens=2048
            )

            content = response.choices[0].message.content.strip()

            # ── 如果期望JSON，在这里做格式检测 ──
            if expect_json:
                ok, result = _validate_filler_json(content)
                if ok:
                    return True, result
                else:
                    last_error = f"JSON格式校验失败：{result}"
                    print(f"[API] 第 {attempt} 次格式校验失败：{result}")
                    # 把错误信息追加到messages里，让下一次重试知道哪里错了
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                                    "role": "user",
                                    # ✅ 优化：把具体错误原因告诉填表员，而不是笼统地说"格式不对"
                                    "content": (
                                        f"你的输出有以下问题，请修正后重新输出：\n"
                                        f"错误原因：{result}\n\n"
                                        f"注意事项：\n"
                                        f"1. 只输出纯JSON，不要有任何其他文字\n"
                                        f"2. time_start 必须是 YYYY-MM-DD HH:MM 格式的字符串，不能是null或空字符串\n"
                                        f"3. 自然语言时间（如'明天下午三点'）必须换算成具体日期时间\n"
                                        f"4. add/update/delete 三个字段都必须存在，没有内容填 []"
                                        )
                                    })
                    time.sleep(RETRY_DELAY)
                    continue

            # 不需要JSON校验，直接返回
            return True, content

        except Exception as e:
            last_error = str(e)
            print(f"[API] 第 {attempt} 次请求异常：{e}")
            time.sleep(RETRY_DELAY)

    return False, last_error


# ════════════════════════════════════════
#  JSON格式校验器（填表员专用）
# ════════════════════════════════════════

def _validate_filler_json(content: str) -> tuple[bool, str]:
    """
    校验填表员输出的JSON是否合法

    返回：
        (True, 清洗后的JSON字符串)  → 合法
        (False, 错误描述)           → 不合法
    """
    # Step1：尝试去掉可能混入的代码块标记
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # 去掉第一行（```json 或 ```）和最后一行（```）
        cleaned = "\n".join(lines[1:-1]).strip()

    # Step2：尝试解析JSON
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return False, f"JSON解析失败：{e}"

    # Step3：检查必须有的顶层字段
    required_keys = {"add", "update", "delete"}
    missing = required_keys - set(data.keys())
    if missing:
        return False, f"缺少必要字段：{missing}"

    # Step4：检查三个字段都必须是列表
    for key in required_keys:
        if not isinstance(data[key], list):
            return False, f"字段 '{key}' 必须是列表，实际类型：{type(data[key]).__name__}"

    # Step5：检查add列表里每条必须有title和time_start
    for i, item in enumerate(data.get("add", [])):
        if "title" not in item:
            return False, f"add[{i}] 缺少必填字段 'title'"
        if "time_start" not in item:
            return False, f"add[{i}] 缺少必填字段 'time_start'"

        # ✅ 修复：先检查类型，再检查格式
        time_val = item["time_start"]
        if time_val is None:
            return False, f"add[{i}].time_start 不能为null，这是必填字段"
        if not isinstance(time_val, str):
            return False, f"add[{i}].time_start 类型错误，必须是字符串，实际是：{type(time_val).__name__}"
        if time_val.strip() == "":
            return False, f"add[{i}].time_start 不能为空字符串，必须填入具体时间"

        ok, msg = _validate_time_format(time_val)
        if not ok:
            return False, f"add[{i}].time_start 时间格式错误：{msg}"

    # ✅ 同样保护 update 里的 time_start（如果有的话）
    for i, item in enumerate(data.get("update", [])):
        if "id" not in item:
            return False, f"update[{i}] 缺少必填字段 'id'"
        if "time_start" in item:
            time_val = item["time_start"]
            if time_val is not None:  # update里time_start允许不传，但传了就必须格式正确
                if not isinstance(time_val, str) or time_val.strip() == "":
                    return False, f"update[{i}].time_start 格式不合法"
                ok, msg = _validate_time_format(time_val)
                if not ok:
                    return False, f"update[{i}].time_start 时间格式错误：{msg}"

    # Step6：检查update和delete列表里每条必须有id
    for i, item in enumerate(data.get("update", [])):
        if "id" not in item:
            return False, f"update[{i}] 缺少必填字段 'id'"

    for i, item in enumerate(data.get("delete", [])):
        if "id" not in item:
            return False, f"delete[{i}] 缺少必填字段 'id'"

    return True, cleaned


def _validate_time_format(time_str: str) -> tuple[bool, str]:
    """校验时间格式是否为 YYYY-MM-DD HH:MM"""
    from datetime import datetime
    try:
        datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        return True, ""
    except ValueError:
        return False, f"'{time_str}' 不符合 YYYY-MM-DD HH:MM 格式"


# ════════════════════════════════════════
#  格式化操作报告（归档成功后发给主人看）
# ════════════════════════════════════════

def _format_report(report: dict) -> str:
    """把 apply_ai_changes 返回的报告格式化成可读消息"""
    lines = ["✅ 日程表已更新！\n"]

    if report["added"]:
        lines.append("📌 **新增**：")
        for item in report["added"]:
            t = item["time_start"]
            if item.get("time_end"):
                t += f" ~ {item['time_end']}"
            lines.append(f"  · [{t}] {item['title']}  🔑{item['id']}")

    if report["updated"]:
        lines.append("\n✏️ **修改**：")
        for item in report["updated"]:
            t = item["time_start"]
            lines.append(f"  · [{t}] {item['title']}  🔑{item['id']}")

    if report["deleted"]:
        lines.append("\n🗑️ **删除**：")
        for item in report["deleted"]:
            lines.append(f"  · {item['title']}  🔑{item['id']}")

    if report["errors"]:
        lines.append("\n⚠️ **部分操作出错**：")
        for err in report["errors"]:
            lines.append(f"  · {err}")

    if not any([report["added"], report["updated"], report["deleted"]]):
        lines.append("（本次没有检测到任何变更哦，如果主人觉得有误，可以重新描述一下～）")

    return "\n".join(lines)


# ════════════════════════════════════════
#  🌟 对外接口：三种消息处理函数
# ════════════════════════════════════════

def handle_chat(user_message: str, image_data: bytes = None) -> str:
    """
    第一种：普通对话模式（阿离）
    user_message: 主人发的文字
    image_data:   主人发的图片原始字节（可选）
    返回阿离的回复文字
    """
    # 如果有图片，转成base64
    image_base64 = None
    if image_data:
        image_base64 = base64.b64encode(image_data).decode("utf-8")

    # 构建消息
    messages = build_chat_messages(user_message, image_base64)

    # 调用API
    ok, content = _call_api(messages, model=MODEL_CHAT, expect_json=False)

    if ok:
        # 把这轮对话存入历史（不存思维链，就存最终回复）
        append_message("user", user_message, msg_type="chat")
        append_message("assistant", content, msg_type="chat")
        return content
    else:
        # API彻底失败了
        err_msg = "呜呜，阿离刚才连接大脑失败了，请主人稍后再试！🦞"
        print(f"[错误] handle_chat API调用失败：{content}")
        return err_msg

from schedule_manager import (
    apply_ai_changes,
    add_repeat_schedule,
    preview_repeat_schedule,
    delete_repeat_series
)

# ════════════════════════════════════════
#  新增：等待确认的重复日程暂存
#  （预览给主人看后，等主人回 y 再真正写入）
# ════════════════════════════════════════
_pending_repeat_rules: list[dict] = []

def handle_confirm(user_id: str) -> str:
    """
    第二种：填表员归档模式
    ✅ 新增对 add_repeat 和 delete_repeat 的处理
    """
    global _pending_repeat_rules

    # ── 如果有等待确认的重复日程，这次y就是确认写入 ──
    if _pending_repeat_rules:
        return _commit_pending_repeats()

    # ── 正常流程：调用填表员 ──
    config = load_config()
    n = config.get("history_n", 6)
    recent = get_recent_messages(n)

    if not recent:
        return "⚠️ 没有找到最近的对话记录，无法归档哦！"

    messages = build_filler_messages(recent)
    ok, content = _call_api(messages, model=MODEL_FILLER, expect_json=True)

    if not ok:
        return "📋 归纳失败！填表员连续出错了，请稍后再试～"

    try:
        changes = json.loads(content)
    except json.JSONDecodeError:
        return "📋 归纳失败！JSON解析出错，请稍后再试～"

    # ── 处理普通增删改（原有逻辑）──
    normal_changes = {
        "add":    changes.get("add",    []),
        "update": changes.get("update", []),
        "delete": changes.get("delete", [])
    }
    report_lines = []

    if any(normal_changes.values()):
        report = apply_ai_changes(normal_changes)
        report_lines.append(_format_report(report))

    # ── 处理 delete_repeat ──
    for item in changes.get("delete_repeat", []):
        rid = item.get("id")
        if rid:
            result = delete_repeat_series(rid)
            report_lines.append(
                f"🗑️ 已删除重复系列（ID: {rid}），"
                f"共清除 {result['deleted_count']} 个提醒点"
            )

    # ── 处理 add_repeat：先预览，等主人确认 ──
    repeat_rules_raw = changes.get("add_repeat", [])
    if repeat_rules_raw:
        previews = []
        for raw in repeat_rules_raw:
            try:
                # 构建规则对象（不写入，只预览）
                import uuid as _uuid
                rule = {
                    "id":               str(_uuid.uuid4())[:8],
                    "type":             "repeat",
                    "title":            raw["title"],
                    "date_start":       raw["date_start"],
                    "date_end":         raw["date_end"],
                    "interval_hours":   float(raw["interval_hours"]),
                    "sleep_start":      raw.get("sleep_start", "23:00"),
                    "sleep_end":        raw.get("sleep_end",   "08:00"),
                    "duration_minutes": int(raw.get("duration_minutes", 0)),
                    "detail":           raw.get("detail"),
                    "tags":             raw.get("tags", []),
                    "status":           "pending",
                    "created_at":       "",
                    "updated_at":       ""
                }
                previews.append(preview_repeat_schedule(rule))
                _pending_repeat_rules.append(rule)

            except KeyError as e:
                report_lines.append(f"⚠️ 重复日程解析失败，缺少字段：{e}")

        if previews:
            # 把普通归档结果（如果有）+ 重复日程预览一起发给主人
            combined = "\n\n".join(filter(None, report_lines + previews))
            # 追加到历史记录
            append_message("user", "y", msg_type="confirm")
            append_message("assistant", combined, msg_type="confirm")
            return combined

    # ── 没有重复日程，正常结束 ──
    append_message("user", "y", msg_type="confirm")
    final = "\n\n".join(filter(None, report_lines)) or "（本次没有检测到任何变更）"
    append_message("assistant", final, msg_type="confirm")
    return final


def _commit_pending_repeats() -> str:
    """
    主人回复 y 确认后，把暂存的重复日程规则全部写入
    """
    global _pending_repeat_rules

    lines = ["✅ 重复日程已全部归档！\n"]

    for rule in _pending_repeat_rules:
        from schedule_manager import commit_repeat_schedule
        from datetime import datetime
        rule["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        rule["updated_at"] = rule["created_at"]

        result = commit_repeat_schedule(rule)
        lines.append(
            f"📌 「{rule['title']}」\n"
            f"   {rule['date_start']} ~ {rule['date_end']}\n"
            f"   每隔 {rule['interval_hours']} 小时\n"
            f"   ✅ 已写入 {result['committed']} 个提醒点"
            + (f"，⚠️ 跳过 {result['skipped']} 个（冲突）" if result['skipped'] else "")
        )

    _pending_repeat_rules.clear()
    return "\n".join(lines)


def handle_reminder(schedule_item: dict) -> str:
    """
    第三种：提醒模式
    schedule_item: 来自 schedule_manager 的单条日程数据
    返回提醒消息文字（发给主人）
    """
    # 默认兜底消息（API彻底失败时用）
    fallback = (
        f"🚨 有新事项！\n"
        f"📌 {schedule_item.get('title', '未知事项')}\n"
        f"⏰ {schedule_item.get('time_start', '时间未知')}\n"
        f"主人快看日程表，别错过啦！🦞"
    )

    # 构建提醒消息
    messages = build_reminder_messages(schedule_item)

    # 调用API（最多重试3次）
    ok, content = _call_api(messages, model=MODEL_CHAT, expect_json=False)

    if not ok:
        print(f"[错误] handle_reminder API失败，使用兜底消息")
        return fallback

    # 简单检测：提醒消息必须包含事项名称，不然没意义
    title = schedule_item.get("title", "")
    if title and title not in content:
        print(f"[警告] 提醒消息未包含事项名称，使用兜底消息")
        return fallback

    return content