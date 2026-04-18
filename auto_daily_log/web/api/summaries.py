"""Summaries API — generate, list, publish, edit, delete (pipeline refactor Phase 2).

Core pipeline: generate_scope(db, engine, scope_name, target_date)
  1. Gather input once (activities + commits for day, or daily summaries for week/month)
  2. Fan-out to all enabled scope_outputs under that scope
  3. For per_issue outputs, expand to one summary row per active issue
  4. Auto-publish if configured
"""
from __future__ import annotations

import json
from datetime import date as date_mod, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ...summarizer.prompt import (
    DEFAULT_AUTO_APPROVE_PROMPT,
    DEFAULT_PERIOD_SUMMARY_PROMPT,
    DEFAULT_SUMMARIZE_PROMPT,
    render_prompt,
)

router = APIRouter(tags=["summaries"])


class GenerateScopeRequest(BaseModel):
    scope_name: str
    target_date: Optional[str] = None  # defaults to today
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    force: bool = False


class SummaryUpdate(BaseModel):
    content: Optional[str] = None
    time_spent_sec: Optional[int] = None
    issue_key: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_scope_period(scope_type: str, target_date: str, start_date=None, end_date=None):
    """Return (period_start, period_end) based on scope_type.

    Calendar-aligned units:
      day     → (target, target)
      week    → (Monday of that week, target)
      month   → (1st of that month, target)
      quarter → (1st of that quarter, target)  Q1=Jan, Q2=Apr, Q3=Jul, Q4=Oct
      custom  → (start_date, end_date) pass-through
    """
    td = date_mod.fromisoformat(target_date)
    if scope_type == "day":
        return target_date, target_date
    elif scope_type == "week":
        start = (td - timedelta(days=td.weekday())).isoformat()
        return start, target_date
    elif scope_type == "month":
        return td.replace(day=1).isoformat(), target_date
    elif scope_type == "quarter":
        q_month = ((td.month - 1) // 3) * 3 + 1  # 1, 4, 7, 10
        return td.replace(month=q_month, day=1).isoformat(), target_date
    elif scope_type == "custom":
        return start_date or target_date, end_date or target_date
    return target_date, target_date


async def _get_llm_engine(db, request):
    """Resolve LLM engine from settings or app state."""
    # Import here to avoid circular imports at module level
    from .worklogs import _get_llm_engine_from_settings
    engine = await _get_llm_engine_from_settings(db)
    if not engine:
        engine = getattr(request.app.state, "_llm_engine", None)
    return engine


async def _gather_daily_input(db, target_date: str) -> dict:
    """Gather raw activities + commits for a single day."""
    activities = await db.fetch_all(
        "SELECT * FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL",
        (target_date,),
    )
    commits = await db.fetch_all(
        "SELECT * FROM git_commits WHERE date = ?", (target_date,)
    )
    return {"activities": activities, "commits": commits, "date": target_date}


async def _gather_period_input(db, start: str, end: str) -> dict:
    """Gather daily summaries for a period (week/month)."""
    daily_summaries = await db.fetch_all(
        "SELECT * FROM summaries s "
        "JOIN scope_outputs o ON s.output_id = o.id "
        "WHERE s.scope_name = 'daily' AND o.output_mode = 'single' "
        "AND s.date >= ? AND s.date <= ? "
        "ORDER BY s.date",
        (start, end),
    )
    # Fallback to worklog_drafts if no summaries migrated yet
    if not daily_summaries:
        daily_summaries = await db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE tag = 'daily' AND date >= ? AND date <= ? ORDER BY date",
            (start, end),
        )
    return {"daily_summaries": daily_summaries, "start": start, "end": end}


def _compress_activities(activities: list[dict]) -> str:
    """Same logic as WorklogSummarizer._compress_activities."""
    if not activities:
        return "无"
    from collections import defaultdict
    groups = defaultdict(lambda: {"duration": 0, "titles": set(), "llm_summaries": [], "ocr_fallback": []})
    for a in activities:
        key = (a.get("category", "other"), a.get("app_name", "Unknown"))
        groups[key]["duration"] += a.get("duration_sec", 0)
        title = a.get("window_title")
        if title:
            groups[key]["titles"].add(title[:60])
        llm_sum = a.get("llm_summary")
        if llm_sum and llm_sum != "(failed)":
            if llm_sum not in groups[key]["llm_summaries"]:
                groups[key]["llm_summaries"].append(llm_sum)
        else:
            if a.get("signals"):
                try:
                    signals = json.loads(a["signals"])
                    ocr = (signals.get("ocr_text") or "")[:100]
                    if ocr and len(groups[key]["ocr_fallback"]) < 3:
                        groups[key]["ocr_fallback"].append(ocr)
                except (json.JSONDecodeError, TypeError):
                    pass
    lines = []
    for (cat, app), info in sorted(groups.items(), key=lambda x: -x[1]["duration"]):
        hours = round(info["duration"] / 3600, 1)
        if hours < 0.1:
            continue
        titles = list(info["titles"])[:5]
        title_str = ", ".join(titles) if titles else ""
        line = f"- [{cat}] {app} ({hours}h): {title_str}"
        if info["llm_summaries"]:
            summaries = "；".join(info["llm_summaries"][:8])
            line += f" | 内容: {summaries}"
        elif info["ocr_fallback"]:
            line += f" | OCR: {'; '.join(info['ocr_fallback'][:2])}"
        lines.append(line)
    return "\n".join(lines) or "无"


def _format_commits(commits: list[dict]) -> str:
    if not commits:
        return "无"
    return "\n".join(
        f"- {c['committed_at'][:16]} {c['message']} ({c.get('files_changed', '')})"
        for c in commits
    )


# ── Core pipeline ──────────���─────────────────────────────────────────


async def generate_scope(db, engine, scope_name: str, target_date: str,
                         start_date=None, end_date=None,
                         *, auto_publish: bool = False) -> list[dict]:
    """Core pipeline: generate all outputs for a scope.

    Returns list of created summary rows.
    """
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = ?", (scope_name,))
    if not scope:
        raise ValueError(f"Scope '{scope_name}' not found")

    outputs = await db.fetch_all(
        "SELECT * FROM scope_outputs WHERE scope_name = ? AND enabled = 1",
        (scope_name,),
    )
    if not outputs:
        return []

    scope_type = scope["scope_type"]
    period_start, period_end = _resolve_scope_period(
        scope_type, target_date, start_date, end_date
    )

    # Dedup: delete existing summaries for the same scope + period.
    existing = await db.fetch_all(
        "SELECT id FROM summaries WHERE scope_name = ? AND period_start = ? AND period_end = ?",
        (scope_name, period_start, period_end),
    )
    for ex in existing:
        await db.execute("DELETE FROM audit_logs WHERE summary_id = ?", (ex["id"],))
        await db.execute("DELETE FROM summaries WHERE id = ?", (ex["id"],))

    # 1. Gather input once
    if scope_type == "day":
        input_data = await _gather_daily_input(db, target_date)
        if not input_data["activities"] and not input_data["commits"]:
            return []
    else:
        input_data = await _gather_period_input(db, period_start, period_end)
        if not input_data["daily_summaries"]:
            return []

    created: list[dict] = []

    # 2. Fan-out to each output (each output can use its own LLM engine)
    from ...summarizer.engine_registry import get_engine_by_name
    for output in outputs:
        try:
            # Per-output engine override; fallback to caller-provided engine, then default
            out_engine = engine
            engine_name = output.get("llm_engine_name")
            if engine_name:
                resolved = await get_engine_by_name(db, engine_name)
                if resolved:
                    out_engine = resolved
            elif not out_engine:
                out_engine = await get_engine_by_name(db, None)

            rows = await _generate_output(
                db, out_engine, scope, output, input_data, target_date, period_start, period_end,
                auto_publish=auto_publish,
            )
            created.extend(rows)
        except Exception as e:
            print(f"[Pipeline] Output #{output['id']} ({output['display_name']}) failed: {e}")

    return created


async def _generate_output(db, engine, scope, output, input_data,
                           target_date, period_start, period_end,
                           *, auto_publish: bool = False) -> list[dict]:
    """Generate summaries for a single output config."""
    if output["output_mode"] == "per_issue":
        return await _generate_per_issue(
            db, engine, scope, output, input_data, target_date, period_start, period_end,
            auto_publish=auto_publish,
        )
    else:
        return await _generate_single(
            db, engine, scope, output, input_data, target_date, period_start, period_end,
            auto_publish=auto_publish,
        )


async def _generate_single(db, engine, scope, output, input_data,
                            target_date, period_start, period_end,
                            *, auto_publish: bool = False) -> list[dict]:
    """Generate a single summary for this output."""
    if scope["scope_type"] == "day":
        content = await _run_daily_single(engine, output, input_data)
    else:
        content = await _run_period_single(engine, output, input_data, scope["scope_type"])

    if not content:
        return []

    summary_id = await db.execute(
        "INSERT INTO summaries "
        "(scope_name, output_id, date, period_start, period_end, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (scope["name"], output["id"], target_date, period_start, period_end, content),
    )
    await db.execute(
        "INSERT INTO audit_logs (summary_id, action, after_snapshot) VALUES (?, 'created', ?)",
        (summary_id, json.dumps({"output": output["display_name"], "length": len(content)}, ensure_ascii=False)),
    )

    row = {"id": summary_id, "scope_name": scope["name"], "output_id": output["id"],
           "date": target_date, "content": content}

    # Auto-publish only when triggered by scheduler
    if auto_publish and output["auto_publish"] and output["publisher_name"]:
        await _auto_publish_summary(db, summary_id, output)

    return [row]


async def _generate_per_issue(db, engine, scope, output, input_data,
                               target_date, period_start, period_end,
                               *, auto_publish: bool = False) -> list[dict]:
    """Generate one summary per active issue."""
    issues = await db.fetch_all("SELECT * FROM jira_issues WHERE is_active = 1")
    if not issues:
        return []

    # Build shared context
    if scope["scope_type"] == "day":
        activities_text = _compress_activities(input_data["activities"])
        commits_text = _format_commits(input_data["commits"])
    else:
        activities_text = _format_period_logs(input_data["daily_summaries"])
        commits_text = ""

    # Build full context summary first (same as step 1 of old pipeline)
    issues_text = "\n".join(
        f"- {i['issue_key']}: {i['summary']} ({i['description'] or ''})"
        for i in issues
    ) or "无"

    # Use the output's prompt_template or fall back to default auto_approve prompt
    template = output.get("prompt_template") or DEFAULT_AUTO_APPROVE_PROMPT
    prompt = render_prompt(
        template,
        date=target_date,
        jira_issues=issues_text,
        full_summary=activities_text,
        git_commits=commits_text,
    )

    if not engine:
        return []

    response = await engine.generate(prompt)
    parsed = _parse_json_array(response)

    # Merge same issue_key entries
    merged: dict[str, dict] = {}
    for item in parsed:
        try:
            hours = float(item.get("time_spent_hours", 0))
        except (TypeError, ValueError):
            continue
        key = item.get("issue_key") or ""
        if not key or key == "OTHER":
            continue  # Discard unmapped entries — already in single output
        summary_text = (item.get("summary") or "").strip()
        if key in merged:
            merged[key]["time_spent_hours"] = round(merged[key]["time_spent_hours"] + hours, 2)
            if summary_text:
                existing = merged[key]["summary"]
                merged[key]["summary"] = f"{existing}；{summary_text}" if existing else summary_text
        else:
            merged[key] = {"issue_key": key, "time_spent_hours": round(hours, 2), "summary": summary_text}

    created = []
    for entry in merged.values():
        time_sec = int(entry["time_spent_hours"] * 3600)
        summary_id = await db.execute(
            "INSERT INTO summaries "
            "(scope_name, output_id, date, period_start, period_end, "
            "issue_key, time_spent_sec, content) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (scope["name"], output["id"], target_date, period_start, period_end,
             entry["issue_key"], time_sec, entry["summary"]),
        )
        await db.execute(
            "INSERT INTO audit_logs (summary_id, action, after_snapshot) VALUES (?, 'created', ?)",
            (summary_id, json.dumps({"issue_key": entry["issue_key"], "hours": entry["time_spent_hours"]}, ensure_ascii=False)),
        )
        row = {"id": summary_id, "scope_name": scope["name"], "output_id": output["id"],
               "date": target_date, "issue_key": entry["issue_key"],
               "time_spent_sec": time_sec, "content": entry["summary"]}
        created.append(row)

        # Auto-publish only when triggered by scheduler
        if auto_publish and output["auto_publish"] and output["publisher_name"]:
            await _auto_publish_summary(db, summary_id, output)

    return created


async def _run_daily_single(engine, output, input_data) -> str:
    """Run LLM for a single daily summary."""
    template = output.get("prompt_template") or DEFAULT_SUMMARIZE_PROMPT
    activities_text = _compress_activities(input_data["activities"])
    commits_text = _format_commits(input_data["commits"])
    prompt = render_prompt(
        template,
        date=input_data["date"],
        activities=activities_text,
        git_commits=commits_text,
    )
    if not engine:
        return f"Activities:\n{activities_text}\n\nCommits:\n{commits_text}"
    try:
        return (await engine.generate(prompt)).strip()
    except Exception as e:
        print(f"[Pipeline] LLM failed for single output, using raw fallback: {e}")
        return f"Activities:\n{activities_text}\n\nCommits:\n{commits_text}"


async def _run_period_single(engine, output, input_data, scope_type) -> str:
    """Run LLM for a period summary (week/month)."""
    daily_text = _format_period_logs(input_data["daily_summaries"])
    if not daily_text:
        return ""
    template = output.get("prompt_template") or DEFAULT_PERIOD_SUMMARY_PROMPT
    period_type_label = {"week": "周报", "month": "月报", "quarter": "季报", "custom": "阶段性总结"}.get(scope_type, "总结")
    prompt = render_prompt(
        template,
        period_start=input_data["start"],
        period_end=input_data["end"],
        period_type=period_type_label,
        daily_logs=daily_text,
    )
    if not engine:
        return daily_text
    return (await engine.generate(prompt)).strip()


def _format_period_logs(logs: list[dict]) -> str:
    """Format daily summaries/drafts into text for period prompt."""
    parts = []
    for log in logs:
        log_date = log.get("date", "")
        content = log.get("content") or log.get("full_summary") or log.get("summary", "")
        parts.append(f"【{log_date}】\n{content}")
    return "\n\n".join(parts)


def _parse_json_array(response: str) -> list[dict]:
    import re
    json_match = re.search(r"\[.*\]", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return []


async def _publish_summary(db, summary_id: int, publisher, publisher_name: str) -> "PublishResult":
    """Core publish logic shared by auto-publish and manual publish."""
    from ...publishers import PublishResult
    summary = await db.fetch_one("SELECT * FROM summaries WHERE id = ?", (summary_id,))
    if not summary:
        return PublishResult(success=False, worklog_id="", platform=publisher_name, raw=None, error="Summary not found")

    issue_key = summary.get("issue_key") or ""
    _SKIP_KEYS = {"ALL", "DAILY"}
    if publisher.name == "jira" and (not issue_key or issue_key in _SKIP_KEYS):
        return PublishResult(success=False, worklog_id="", platform=publisher_name, raw=None, error=f"Issue key '{issue_key}' 不支持推送")

    started = f"{summary['date']}T21:00:00.000+0800"
    result = await publisher.submit(
        issue_key=issue_key,
        time_spent_sec=summary.get("time_spent_sec") or 0,
        comment=summary.get("content") or "",
        started=started,
    )
    if result.success:
        await db.execute(
            "UPDATE summaries SET published_id = ?, published_at = datetime('now'), "
            "publisher_name = ? WHERE id = ?",
            (result.worklog_id, publisher_name, summary_id),
        )
        await db.execute(
            "INSERT INTO audit_logs (summary_id, action, after_snapshot) VALUES (?, 'published', ?)",
            (summary_id, json.dumps({"publisher": publisher_name, "worklog_id": result.worklog_id}, ensure_ascii=False)),
        )
    return result


async def _auto_publish_summary(db, summary_id: int, output: dict):
    """Auto-publish a summary to its configured publisher."""
    from ...publishers.registry import get_publisher_for_output
    try:
        publisher = await get_publisher_for_output(db, output["id"])
        if not publisher:
            return
        await _publish_summary(db, summary_id, publisher, output["publisher_name"])
    except Exception as e:
        print(f"[Pipeline] Auto-publish failed for summary #{summary_id}: {e}")


# ── API endpoints ───────────────���────────────────────────────────────


@router.post("/summaries/generate")
async def generate_summary(body: GenerateScopeRequest, request: Request):
    """Generate all outputs for a scope."""
    db = request.app.state.db

    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = ?", (body.scope_name,))
    if not scope:
        raise HTTPException(404, f"总结周期 '{body.scope_name}' 不存在")

    target = body.target_date or date_mod.today().isoformat()
    period_start, period_end = _resolve_scope_period(
        scope["scope_type"], target, body.start_date, body.end_date
    )

    # Delete existing summaries for this scope+period if force
    if body.force:
        existing = await db.fetch_all(
            "SELECT id FROM summaries WHERE scope_name = ? AND date = ? AND period_start = ? AND period_end = ?",
            (body.scope_name, target, period_start, period_end),
        )
        for ex in existing:
            await db.execute("DELETE FROM audit_logs WHERE summary_id = ?", (ex["id"],))
            await db.execute("DELETE FROM summaries WHERE id = ?", (ex["id"],))
    else:
        existing = await db.fetch_one(
            "SELECT id FROM summaries WHERE scope_name = ? AND date = ? AND period_start = ? AND period_end = ? LIMIT 1",
            (body.scope_name, target, period_start, period_end),
        )
        if existing:
            raise HTTPException(409, "该范围和日期的总结已存在，使用 force=true 覆盖")

    # Activity backfill (same as old summarizer)
    activity_summarizer = getattr(request.app.state, "activity_summarizer", None)
    if activity_summarizer and scope["scope_type"] == "day":
        try:
            processed = await activity_summarizer.backfill_for_date(target, timeout_sec=60)
            print(f"[Pipeline] Activity backfill processed {processed} row(s)")
        except Exception as e:
            print(f"[Pipeline] Activity backfill failed (non-fatal): {e}")

    # Git collect
    if scope["scope_type"] == "day":
        try:
            from ...collector.git_collector import GitCollector
            collector = GitCollector(db)
            await collector.collect_today()
        except Exception as e:
            print(f"[Pipeline] Git collect failed (non-fatal): {e}")

    engine = await _get_llm_engine(db, request)
    created = await generate_scope(db, engine, body.scope_name, target,
                                   body.start_date, body.end_date)

    # Also write to worklog_drafts for backward compat during transition
    await _dual_write_drafts(db, created, body.scope_name, target, period_start, period_end)

    return {"scope_name": body.scope_name, "date": target,
            "period_start": period_start, "period_end": period_end,
            "summaries_created": len(created),
            "summaries": created}


async def _dual_write_drafts(db, created: list[dict], scope_name: str,
                              target_date: str, period_start: str, period_end: str):
    """Write to worklog_drafts for backward compatibility during Phase 2-3 transition."""
    if not created:
        return

    # Collect single + per_issue summaries
    single_content = ""
    issue_entries = []
    for s in created:
        if s.get("issue_key"):
            hours = round((s.get("time_spent_sec") or 0) / 3600, 2)
            issue_entries.append({
                "issue_key": s["issue_key"],
                "time_spent_hours": hours,
                "summary": s.get("content", ""),
                "jira_worklog_id": None,
            })
        else:
            single_content = s.get("content", "")

    # Delete old drafts for this scope+date
    await db.execute(
        "DELETE FROM worklog_drafts WHERE tag = ? AND date = ?",
        (scope_name, target_date),
    )

    if scope_name == "daily":
        total_sec = sum(int(e["time_spent_hours"] * 3600) for e in issue_entries)
        summary_json = json.dumps(issue_entries, ensure_ascii=False) if issue_entries else "[]"
        await db.execute(
            "INSERT INTO worklog_drafts "
            "(date, issue_key, time_spent_sec, summary, full_summary, status, tag, period_start, period_end) "
            "VALUES (?, 'DAILY', ?, ?, ?, 'pending_review', ?, ?, ?)",
            (target_date, total_sec, summary_json, single_content,
             scope_name, period_start, period_end),
        )
    else:
        content = single_content or json.dumps(
            [{"issue_key": e["issue_key"], "summary": e["summary"]} for e in issue_entries],
            ensure_ascii=False,
        )
        await db.execute(
            "INSERT INTO worklog_drafts "
            "(date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
            "VALUES (?, 'SUMMARY', 0, ?, 'archived', ?, ?, ?)",
            (target_date, content, scope_name, period_start, period_end),
        )


@router.get("/summaries")
async def list_summaries(
    request: Request,
    scope_name: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None),
    output_id: Optional[int] = Query(default=None),
):
    db = request.app.state.db
    sql = "SELECT s.*, o.display_name as output_display_name, o.output_mode, o.publisher_name as output_publisher " \
          "FROM summaries s JOIN scope_outputs o ON s.output_id = o.id WHERE 1=1"
    params: list = []
    if scope_name:
        sql += " AND s.scope_name = ?"
        params.append(scope_name)
    if date:
        sql += " AND s.date = ?"
        params.append(date)
    if output_id:
        sql += " AND s.output_id = ?"
        params.append(output_id)
    sql += " ORDER BY s.date DESC, s.output_id, s.issue_key"
    return await db.fetch_all(sql, tuple(params))


@router.get("/summaries/{summary_id}")
async def get_summary(summary_id: int, request: Request):
    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT s.*, o.display_name as output_display_name, o.output_mode, o.publisher_name as output_publisher "
        "FROM summaries s JOIN scope_outputs o ON s.output_id = o.id WHERE s.id = ?",
        (summary_id,),
    )
    if not row:
        raise HTTPException(404, "Summary not found")
    return row


@router.patch("/summaries/{summary_id}")
async def update_summary(summary_id: int, body: SummaryUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM summaries WHERE id = ?", (summary_id,))
    if not existing:
        raise HTTPException(404, "Summary not found")
    updates: list[str] = []
    params: list = []
    if body.content is not None:
        updates.append("content = ?")
        params.append(body.content)
    if body.time_spent_sec is not None:
        updates.append("time_spent_sec = ?")
        params.append(body.time_spent_sec)
    if body.issue_key is not None:
        updates.append("issue_key = ?")
        params.append(body.issue_key)
    if not updates:
        raise HTTPException(400, "没有要更新的字段")
    params.append(summary_id)
    before = json.dumps(dict(existing), ensure_ascii=False, default=str)
    await db.execute(f"UPDATE summaries SET {', '.join(updates)} WHERE id = ?", tuple(params))
    await db.execute(
        "INSERT INTO audit_logs (summary_id, action, before_snapshot) VALUES (?, 'edited', ?)",
        (summary_id, before),
    )
    return {"status": "updated"}


@router.post("/summaries/{summary_id}/publish")
async def publish_summary(summary_id: int, request: Request):
    """Manually publish a summary to its output's configured publisher."""
    db = request.app.state.db
    summary = await db.fetch_one(
        "SELECT s.*, o.publisher_name as output_publisher, o.publisher_config "
        "FROM summaries s JOIN scope_outputs o ON s.output_id = o.id WHERE s.id = ?",
        (summary_id,),
    )
    if not summary:
        raise HTTPException(404, "Summary not found")
    if not summary["output_publisher"]:
        raise HTTPException(400, "该输出没有配置推送平台")
    if summary.get("published_id"):
        raise HTTPException(400, "已推送过，不能重复推送")

    from ...publishers.registry import get_publisher_for_output
    from ...jira_client.client import MissingJiraConfig
    try:
        publisher = await get_publisher_for_output(db, summary["output_id"])
    except MissingJiraConfig as e:
        raise HTTPException(400, str(e))
    if not publisher:
        raise HTTPException(400, "无法创建推送器")

    result = await _publish_summary(db, summary_id, publisher, summary["output_publisher"])
    if not result.success:
        raise HTTPException(502, f"推送失败: {result.error}")
    return {"status": "published", "worklog_id": result.worklog_id}


@router.delete("/summaries/{summary_id}")
async def delete_summary(summary_id: int, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT id FROM summaries WHERE id = ?", (summary_id,))
    if not existing:
        raise HTTPException(404, "Summary not found")
    await db.execute(
        "INSERT INTO audit_logs (summary_id, action) VALUES (?, 'deleted')", (summary_id,)
    )
    await db.execute("DELETE FROM summaries WHERE id = ?", (summary_id,))
    return {"status": "deleted"}


@router.get("/summaries/{summary_id}/audit")
async def get_summary_audit(summary_id: int, request: Request):
    db = request.app.state.db
    return await db.fetch_all(
        "SELECT * FROM audit_logs WHERE summary_id = ? ORDER BY created_at",
        (summary_id,),
    )
