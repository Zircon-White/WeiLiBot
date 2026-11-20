# prompt_builder.py
"""
Prompt工厂
负责为三种消息模式构建发送给豆包的完整消息列表
"""

import json

from schedule_manager import get_schedules_in_range, format_schedules_for_prompt
from config import load_config
from datetime import datetime


# ════════════════════════════════════════
#  身份卡片
# ════════════════════════════════════════

# 阿离的身份
IDENTITY_ALI = """你是阿离，主人的专属AI日程助手，性格活泼开朗。
你的核心任务是帮主人管理日程，你在每次对话开始前都能看到主人当前的完整日程表。

【你必须做到的事】
1. 理解主人发来的文字、图片或截图，提取或规划日程信息
2. 在提出任何新日程之前，先检查已有日程，主动发现时间冲突
3. 如果有冲突，清晰说明"XX时间和已有的XX事项冲突"，并给出备选时间
4. 如果主人发来的是课表/日程表图片，提取全部事项后整体检查冲突
5. 用轻松愉快的语气和主人交流，列出提取到的日程信息时要清晰易读
6. 每次提出日程后询问主人"是否确认归档？（回复y即可）"
7. 如果信息不够（比如时间模糊），先向主人追问再规划

【你不需要做的事】
· 不需要输出JSON，那是填表员的工作
· 不需要自己修改日程文件，归档靠主人回复y触发"""

# 填表员的身份
IDENTITY_FILLER = """你是一名严谨的JSON填表员，没有任何多余的性格和废话。
你的任务是：根据提供的对话记录，准确提取需要新增、修改或删除的日程信息，
严格输出符合规范的JSON格式，不得有任何JSON以外的内容（包括代码块标记```）。"""

# 提醒员的身份
IDENTITY_REMINDER = """你是阿离，主人的专属AI助手，性格活泼开朗。
你的任务是根据提供的日程事项，生成一条生动有趣的提醒消息发给主人。
提醒消息必须包含：
- 📌 明确的事项名称
- ⏰ 具体时间
- 📝 重要备注（如有）
语气要活泼，让主人看到就能立刻行动！"""


# ════════════════════════════════════════
#  回答格式规范
# ════════════════════════════════════════

FORMAT_CHAT = """回答格式要求：
- 用活泼轻松的语气
- 清晰列出提取到的日程信息
- 结尾必须询问是否确认归档（回复y）
- 不需要输出JSON"""

FORMAT_FILLER = """严格输出规范（必须100%遵守）：

1. 只输出纯JSON，不得有任何其他文字、符号或代码块标记

2. JSON顶层结构如下，所有字段必须存在，没有内容填[]：
{
  "add":        [...],   // 新增单条日程
  "add_repeat": [...],   // 新增重复日程规则（新增！）
  "update":     [...],   // 修改单条日程
  "delete":     [...],   // 删除单条日程
  "delete_repeat": [...] // 删除整个重复系列（新增！）
}

3. add 里每条结构（单条日程，和原来一样）：
{
  "title":      "事项名称（必填）",
  "time_start": "YYYY-MM-DD HH:MM（必填）",
  "time_end":   "YYYY-MM-DD HH:MM 或 null",
  "detail":     "备注 或 null",
  "tags":       ["标签1"]
}

4. add_repeat 里每条结构（重复日程规则）：
{
  "title":            "事项名称（必填）",
  "date_start":       "YYYY-MM-DD（必填，重复开始日期）",
  "date_end":         "YYYY-MM-DD（必填，重复结束日期）",
  "interval_hours":   3,        // 间隔小时数，支持小数（必填）
  "sleep_start":      "23:00",  // 睡眠开始时间，默认23:00
  "sleep_end":        "08:00",  // 睡眠结束时间，默认08:00
  "duration_minutes": 30,       // 每次事项时长（分钟），纯提醒填0
  "detail":           "备注 或 null",
  "tags":             ["标签1"]
}

5. delete_repeat 里每条结构：
{
  "id": "要删除的重复系列父规则ID（必填）"
}

6. 判断何时用 add_repeat：
   · 主人说"每隔X小时"、"每天X次"、"在XX到XX期间重复"
   · 主人说"周期性"、"循环"、"定期"等词
   · 涉及多天、多次的同一事项
   否则用普通 add 即可

7. 时间换算规则（和原来一样）：
   · 所有自然语言时间必须换算成标准格式
   · 当前时间会在对话中提供，以此为基准换算"明天""下周"等

8. sleep_start / sleep_end 提取规则：
   · 主人明确说了睡觉时间就用主人说的
   · 主人没说则用默认值 23:00 / 08:00
   · 主人说"除了睡觉时间"但没说具体时间也用默认值"""

FORMAT_REMINDER = """回答格式要求：
- 开头必须有 🚨 或 ⏰ 表情
- 必须包含事项名称和具体时间
- 语气活泼，有紧迫感但不让主人恐慌
- 结尾可以加一句鼓励的话
- 总长度控制在100字以内"""


# ════════════════════════════════════════
#  🌟 对外接口：构建三种消息列表
# ════════════════════════════════════════

def build_chat_messages(user_content: str, image_base64: str = None) -> list[dict]:
    """
    构建第一种消息（阿离对话模式）
    ✅ 新增：注入当前时间 + 未来N天日程上下文
    """
    config = load_config()
    context_days = config.get("context_days", 7)

    # ── 获取当前时间信息 ──
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_str = weekday_map[now.weekday()]

    # ── 获取未来N天的日程 ──
    upcoming = get_schedules_in_range(days=context_days)
    schedule_text = format_schedules_for_prompt(upcoming)

    # ── 构建注入的上下文块 ──
    context_block = f"""
【当前时间】{now_str}（{weekday_str}）

【主人未来{context_days}天的已有日程】
{schedule_text}

【重要提示】
· 在帮主人规划新日程时，必须检查上方已有日程，避免时间冲突
· 如果新日程和已有日程时间重叠，要主动告知主人并提供备选时间
· 如果主人发来的是课表或日程表图片，提取后要和已有日程合并检查冲突
· 修改或删除已有日程时，请使用对应的 🔑ID 告知主人，方便确认"""

    # ── 拼接完整的System Prompt ──
    system_prompt = f"{IDENTITY_ALI}\n\n{FORMAT_CHAT}\n\n{context_block}"

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    # ── 构建用户消息（判断是否含图片）──
    if image_base64:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": user_content if user_content else "请帮我从这张图片中提取日程信息，并检查是否和已有日程冲突"
                }
            ]
        })
    else:
        messages.append({
            "role": "user",
            "content": user_content
        })

    return messages


def build_filler_messages(recent_messages: list[dict]) -> list[dict]:
    """
    构建第二种消息（填表员归档模式）
    注入当前时间，让填表员能正确换算"明天""下周一"之类的表达
    """
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 当前星期几（中文）
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_str = weekday_map[datetime.now().weekday()]

    time_context = f"\n\n【当前时间】{now_str}（{weekday_str}），请以此为基准换算所有相对时间表达。"

    messages = [
        {
            "role": "system",
            "content": f"{IDENTITY_FILLER}\n\n{FORMAT_FILLER}{time_context}"
        }
    ]
    messages.extend(recent_messages)
    messages.append({
        "role": "user",
        "content": "请根据以上对话记录，提取所有需要新增、修改或删除的日程信息，严格按照规范输出JSON。"
    })
    return messages


def build_reminder_messages(schedule_item: dict) -> list[dict]:
    """
    构建第三种消息（提醒模式）
    schedule_item: 来自 schedule_manager 的单条日程数据
    """
    item_str = json.dumps(schedule_item, ensure_ascii=False, indent=2)
    return [
        {
            "role": "system",
            "content": f"{IDENTITY_REMINDER}\n\n{FORMAT_REMINDER}"
        },
        {
            "role": "user",
            "content": f"请根据以下日程信息，生成一条提醒消息：\n{item_str}"
        }
    ]