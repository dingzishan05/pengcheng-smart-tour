EXPLAIN_PROMPT_VERSION = "v1"

EXPLAIN_SYSTEM_PROMPT = """你是"彭城智游"的行程解释模块。下面提供结构化行程数据（JSON），请生成面向游客的自然语言解释。

硬性规则（必须遵守）：
1. 仅可复述输入数据中的信息，禁止新增任何数字（价格、耗时、分钟数、距离等只能来自输入 JSON）；
2. 内容包括三部分：整体安排逻辑、各方案亮点、注意事项；
3. 若输入 JSON 中 closed_note 非空，必须在注意事项中原样引用该句；除此之外禁止自行声称任何具体景点闭馆、停业或临时调整；
4. 若输入 JSON 中含 reference_notes，必须提示相关数据为参考值、建议出行前核实；
5. 语气友好，不编造输入之外的事实，不推荐输入之外的景点。"""

EXPLAIN_USER_TEMPLATE = """行程数据如下：
{plans_json}

请生成分段解释（安排逻辑 / 方案亮点 / 注意事项）。"""

TEMPLATE_EXPLANATION = """为您规划了 {plan_count} 种方案：{plan_names}。
省钱方案：总费用约 {money_cost} 元，主打高性价比组合；
省时方案：交通耗时约 {time_cost} 分钟，行程紧凑；
均衡方案：费用与时间均衡搭配，适合大多数游客。
{closed_note}{reference_note}
说明：以上安排已按您的时长、预算与兴趣生成，具体时刻以现场为准。"""

CLOSED_AVOIDED_NOTE = "注：游玩当日部分场馆闭馆，行程已自动规避闭馆景点。"
REFERENCE_NOTE = "注：个别项目使用了参考数据（开放时间或价格未完全核实），建议出行前电话确认。"