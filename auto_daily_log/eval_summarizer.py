"""Offline accuracy harness for the summarizer pipeline.

Runs the three pure stages (single-activity guess, daily full-summary,
per-issue refine) against a hand-curated ground-truth dataset and reports
pass/fail per case. No DB, no scheduler — just `summarizer.core` +
whatever EngineCall you hand it.

Usage
-----

    # Smoke test the harness with a deterministic fake engine
    python -m auto_daily_log.eval_summarizer --mock

    # Real eval with the engine marked is_default=1 in the user's DB
    python -m auto_daily_log.eval_summarizer --db ~/.auto_daily_log/data.db

    # Real eval with a specific engine row
    python -m auto_daily_log.eval_summarizer --db ~/.auto_daily_log/data.db --engine kimi

    # Custom dataset
    python -m auto_daily_log.eval_summarizer --dataset path/to/cases.json --mock

The dataset format is documented in tests/fixtures/eval/baseline.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .summarizer import core
from .summarizer.prompt import (
    DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    DEFAULT_AUTO_APPROVE_PROMPT,
    DEFAULT_SUMMARIZE_PROMPT,
)

DEFAULT_DATASET = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "eval" / "baseline.json"
)


# ─── Result types ────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case_id: str
    stage: str          # "activity" | "daily" | "issues"
    passed: bool
    output: Any         # raw LLM output (string or list[dict])
    failures: list[str] = field(default_factory=list)


@dataclass
class StageReport:
    stage: str
    total: int
    passed: int
    cases: list[CaseResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass
class EvalReport:
    stages: list[StageReport]

    @property
    def total(self) -> int:
        return sum(s.total for s in self.stages)

    @property
    def passed(self) -> int:
        return sum(s.passed for s in self.stages)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


# ─── Stage evaluators ────────────────────────────────────────────────────

async def evaluate_activity_case(case: dict, engine_call, template: str) -> CaseResult:
    output = await core.summarize_activity(
        case["record"], case.get("prev_summaries", []),
        engine_call, template,
    )
    expected = case.get("expected", {})
    failures: list[str] = []
    if expected.get("must_not_be_failed", True) and output == core.FAILED_MARKER:
        failures.append("output is the (failed) sentinel")

    keywords = expected.get("must_contain_any_of") or []
    if keywords and not any(k in output for k in keywords):
        failures.append(f"output missing all of {keywords!r}: got {output!r}")

    return CaseResult(
        case_id=case["id"], stage="activity",
        passed=not failures, output=output, failures=failures,
    )


async def evaluate_daily_case(case: dict, engine_call, template: str) -> CaseResult:
    activities_text = core.compress_activities(case.get("activities", []))
    commits_text = core.format_commits(case.get("commits", []))
    output = await core.generate_full_summary(
        case["date"], activities_text, commits_text, engine_call, template,
    )
    expected = case.get("expected", {})
    failures: list[str] = []
    if not output:
        failures.append("empty full_summary output")

    keywords = expected.get("must_contain_any_of") or []
    if keywords and output and not any(k in output for k in keywords):
        failures.append(f"summary missing all of {keywords!r}")

    forbidden = expected.get("must_not_contain") or []
    for f in forbidden:
        if f in output:
            failures.append(f"summary unexpectedly mentions {f!r}")

    return CaseResult(
        case_id=case["id"], stage="daily",
        passed=not failures, output=output, failures=failures,
    )


async def evaluate_issues_case(
    case: dict, engine_call, template: str,
) -> CaseResult:
    issues_text = core.format_jira_issues(case.get("issues", []))
    output = await core.generate_issue_entries(
        case["date"], case["full_summary"], case.get("commits_text", "无"),
        issues_text, engine_call, template,
    )
    expected = case.get("expected", {})
    failures: list[str] = []
    keys = {e["issue_key"] for e in output}

    for required in expected.get("must_have_keys") or []:
        if required not in keys:
            failures.append(f"missing required issue_key {required!r} in {sorted(keys)}")

    for forbidden in expected.get("must_not_have_keys") or []:
        if forbidden in keys:
            failures.append(f"unexpected issue_key {forbidden!r} present")

    if expected.get("min_total_hours") is not None or expected.get("max_total_hours") is not None:
        total = sum(e["time_spent_hours"] for e in output)
        lo = expected.get("min_total_hours")
        hi = expected.get("max_total_hours")
        if lo is not None and total < lo:
            failures.append(f"total hours {total} below min {lo}")
        if hi is not None and total > hi:
            failures.append(f"total hours {total} above max {hi}")

    return CaseResult(
        case_id=case["id"], stage="issues",
        passed=not failures, output=output, failures=failures,
    )


# ─── Top-level orchestration ─────────────────────────────────────────────

async def evaluate_dataset(
    dataset: dict,
    engine_call,
    *,
    activity_template: str = DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    daily_template: str = DEFAULT_SUMMARIZE_PROMPT,
    issues_template: str = DEFAULT_AUTO_APPROVE_PROMPT,
    stages: Optional[list[str]] = None,
) -> EvalReport:
    """Run the eval and return a structured report.

    Caller controls which stages run (default: all three) so a fast
    iteration loop on prompt design can target one stage at a time."""
    enabled = set(stages or ["activity", "daily", "issues"])
    reports: list[StageReport] = []

    if "activity" in enabled and dataset.get("activities"):
        results = []
        for case in dataset["activities"]:
            results.append(await evaluate_activity_case(case, engine_call, activity_template))
        reports.append(StageReport(
            stage="activity", total=len(results),
            passed=sum(1 for r in results if r.passed), cases=results,
        ))

    if "daily" in enabled and dataset.get("dailies"):
        results = []
        for case in dataset["dailies"]:
            results.append(await evaluate_daily_case(case, engine_call, daily_template))
        reports.append(StageReport(
            stage="daily", total=len(results),
            passed=sum(1 for r in results if r.passed), cases=results,
        ))

    if "issues" in enabled and dataset.get("issues"):
        results = []
        for case in dataset["issues"]:
            results.append(await evaluate_issues_case(case, engine_call, issues_template))
        reports.append(StageReport(
            stage="issues", total=len(results),
            passed=sum(1 for r in results if r.passed), cases=results,
        ))

    return EvalReport(stages=reports)


# ─── Engines ─────────────────────────────────────────────────────────────

class MockEngine:
    """Deterministic fake — used to verify the harness wiring without
    burning real API credits. Returns canned content shaped to satisfy
    baseline.json so a green run proves the pipeline plumbing works.
    Real evaluation is meaningless against this engine."""

    async def __call__(self, prompt: str) -> str:
        # Order matters: check the most-specific markers first.
        if "Jira 工时日志助手" in prompt or "适合提交到 Jira" in prompt:
            # Stage: per-issue refine. Pull issue keys mentioned in the prompt.
            keys = []
            for line in prompt.splitlines():
                line = line.strip()
                if line.startswith("- ") and ":" in line:
                    head = line[2:].split(":", 1)[0].strip()
                    if head and head[0].isalpha() and "-" in head:
                        keys.append(head)
            if not keys:
                return "[]"
            entries = [{
                "issue_key": k, "time_spent_hours": 2.5,
                "summary": f"针对 {k} 的工作",
            } for k in keys]
            return json.dumps(entries, ensure_ascii=False)

        if "工作日志助手" in prompt:
            # Stage: full daily summary.
            return (
                "## 今日工作\n\n上午在 VSCode 调试 main.py，"
                "下午参加 Sprint Planning 会议。\n\n"
                "## 其他\n\n看了一会儿 B 站。"
            )

        # Stage: single-activity guess. Echo the dominant signal.
        if "main.py" in prompt:
            return "继续在 VSCode 调试 main.py"
        if "PROJ-101" in prompt or "Jira" in prompt:
            return "在 Jira 上查看 PROJ-101 任务"
        if "Sprint" in prompt or "研发组" in prompt:
            return "在企业微信参加研发组 Sprint 会议"
        return "正在工作"


async def _build_db_engine_call(db_path: Path, engine_name: Optional[str]):
    """Lazy import — pulling Database in pulls aiosqlite, which we don't
    need for the --mock path (CI / smoke tests)."""
    from .models.database import Database
    from .summarizer.engine_registry import get_engine_by_name

    db = Database(db_path)
    await db.initialize()
    engine = await get_engine_by_name(db, engine_name)
    if engine is None:
        await db.close()
        raise SystemExit(
            f"No engine found in {db_path} (name={engine_name or 'default'}). "
            f"Configure one in Settings → 引擎管理."
        )

    async def call(prompt: str) -> str:
        return await engine.generate(prompt)

    return call, db


# ─── CLI ─────────────────────────────────────────────────────────────────

def _print_report(report: EvalReport, verbose: bool) -> None:
    print()
    for stage in report.stages:
        rate = stage.pass_rate * 100
        print(f"=== {stage.stage}: {stage.passed}/{stage.total} ({rate:.0f}%) ===")
        for r in stage.cases:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.case_id}")
            if not r.passed:
                for f in r.failures:
                    print(f"      - {f}")
            if verbose:
                preview = r.output if isinstance(r.output, str) else json.dumps(r.output, ensure_ascii=False)
                preview = preview if len(str(preview)) < 200 else str(preview)[:200] + "…"
                print(f"      output: {preview}")
    print()
    print(f"OVERALL: {report.passed}/{report.total} ({report.pass_rate * 100:.0f}%)")


def _report_to_dict(report: EvalReport) -> dict:
    return {
        "total": report.total,
        "passed": report.passed,
        "pass_rate": report.pass_rate,
        "stages": [{
            "stage": s.stage,
            "total": s.total,
            "passed": s.passed,
            "pass_rate": s.pass_rate,
            "cases": [asdict(c) for c in s.cases],
        } for s in report.stages],
    }


async def _amain(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset).expanduser().resolve()
    if not dataset_path.exists():
        print(f"dataset not found: {dataset_path}", file=sys.stderr)
        return 2
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    db = None
    try:
        if args.mock:
            engine_call = MockEngine()
        else:
            if not args.db:
                print("--db required when not using --mock", file=sys.stderr)
                return 2
            engine_call, db = await _build_db_engine_call(
                Path(args.db).expanduser().resolve(), args.engine,
            )

        stages = args.stages.split(",") if args.stages else None
        report = await evaluate_dataset(dataset, engine_call, stages=stages)
    finally:
        if db is not None:
            await db.close()

    _print_report(report, verbose=args.verbose)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report written to {args.json_out}")

    return 0 if report.passed == report.total else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dataset", default=str(DEFAULT_DATASET),
                   help="path to JSON dataset (default: tests/fixtures/eval/baseline.json)")
    p.add_argument("--db", help="sqlite path; required unless --mock")
    p.add_argument("--engine", help="engine name in llm_engines table; default: is_default=1")
    p.add_argument("--mock", action="store_true",
                   help="use deterministic mock engine (validates wiring only)")
    p.add_argument("--stages", help="comma list: activity,daily,issues (default: all)")
    p.add_argument("--json-out", help="write structured report to this path")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print each case's output")
    args = p.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
