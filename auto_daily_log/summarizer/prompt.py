DEFAULT_SUMMARIZE_PROMPT = """你是工作日志助手。以下是用户今天的工作数据：

【日期】{date}

【活跃 Jira 任务】
{jira_issues}

【Git Commits】
{git_commits}

【活动记录】
{activities}

请为每个 Jira 任务生成：
1. 工时（小时，精确到 0.5h）
2. 工作日志摘要（中文，50-100字，描述具体做了什么）

无法匹配到任何 Jira 任务的活动，归入"未分类"。

以 JSON 格式返回：
[
  {{
    "issue_key": "PROJ-101",
    "time_spent_hours": 3.5,
    "summary": "..."
  }}
]"""


DEFAULT_AUTO_APPROVE_PROMPT = """你是工作日志审批助手。请检查以下工作日志草稿：

【日期】{date}
【Jira 任务】{issue_key}: {issue_summary}
【工时】{time_spent_hours} 小时
【日志内容】{summary}
【关联 Git Commits】{git_commits}

请判断：
1. 日志内容是否与 Git commits 和任务描述一致？
2. 工时是否合理？
3. 日志描述是否清晰、具体？

如果合格返回 {{"approved": true}}
如果不合格返回 {{"approved": false, "reason": "不通过原因"}}"""


def render_prompt(template: str, **kwargs) -> str:
    return template.format(**kwargs)
