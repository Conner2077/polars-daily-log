DEFAULT_SUMMARIZE_PROMPT = """你是工作日志助手。以下是用户今天的所有活动原始数据。请**如实记录**，不筛选、不美化。

【日期】{date}

【Git Commits】
{git_commits}

【活动记录】
{activities}

请写一份纯文本日志，客观、完整地描述今天发生的所有事情。

规则：
- **保留所有活动**：编码、开会、沟通、浏览、看视频、游戏、摸鱼等都要写进去
- 按**时间顺序**或**活动类型**组织，读起来像"今天流水账"
- 每类活动说明：用了什么 app、具体做了什么、涉及什么内容（仓库名、群名、网站名、视频名等）
- 对应的 OCR 内容、URL 可以作为佐证
- 工时用自然语言描述即可（例如"约 2 小时"），不需要精确到 0.5h
- 不做"是否是工作"的主观判断，审批环节会做筛选
- 中文书写，500-1500 字，内容详实

**只输出总结文本**，不要 JSON，不要标题，直接开始正文。"""


DEFAULT_AUTO_APPROVE_PROMPT = """你是 Jira 工时日志助手。基于用户今天的完整活动日志，为每个 Jira 任务生成适合提交到 Jira 的工时条目。

【日期】{date}

【活跃 Jira 任务】
{jira_issues}

【当天完整活动日志】
{full_summary}

【关联 Git Commits】
{git_commits}

请从完整日志中**筛选出工作相关内容**，按 Jira 任务归类润色，产出可直接提交到 Jira 的工时记录。

筛选规则（**排除以下内容**，不计入任何 issue）：
- 娱乐：看视频网站（哔哩哔哩/YouTube/抖音）、游戏、综艺、购物
- 社交：刷微博/小红书/朋友圈、非工作相关的微信/QQ 闲聊
- 新闻、闲逛网页
- 个人事务（订餐、外卖、银行等）

归类规则：
- 有活跃 Jira 任务时：根据活动内容（关键词、涉及的仓库/页面/群名）匹配到对应 issue_key
  - 参考每个任务的标题和描述，判断哪些工作属于这个任务
  - 明确与某任务相关的 → 该任务的 issue_key
  - 无法归到具体任务但是**工作相关** → issue_key = "OTHER"
- 无活跃 Jira 任务时：所有工作内容合并到一条，issue_key = "ALL"
- 同一 issue_key 合并为一条，不拆分

润色规则：
- 每条 summary 精炼、专业（50-150 字），适合提交到 Jira
- 工时精确到 0.5h，只算**工作相关**时长
- 如果当天完全没有工作活动，返回空数组 []

以 JSON 数组格式返回：
[
  {
    "issue_key": "PROJ-101 或 ALL 或 OTHER",
    "time_spent_hours": 3.5,
    "summary": "专业简洁的工作描述..."
  }
]"""


DEFAULT_PERIOD_SUMMARY_PROMPT = """你是工作周报/月报助手。以下是用户在 {period_start} ~ {period_end} 期间的每日工作日志：

{daily_logs}

请生成一份{period_type}总结，要求：
1. 按主要工作方向分类汇总（如：功能开发、Bug修复、会议沟通、调研学习等）
2. 每个方向列出具体做了什么，不要泛泛而谈
3. 总结本周期的工作亮点和主要成果
4. 统计总工时
5. 用中文，200-500字

以纯文本格式返回，不需要 JSON。"""


def render_prompt(template: str, **kwargs) -> str:
    """Safe template rendering — uses simple string replacement instead of .format()
    to avoid issues with curly braces in user data (OCR text, commit messages, etc.)."""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result
