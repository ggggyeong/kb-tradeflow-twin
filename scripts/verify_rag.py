"""Inspect actual queries/hits, then optionally run the thirteen-call live Finance Agent.

This isolates RAG using the synthetic fixture's expected receipt date. It does not
claim to verify OCR; run run_example.py --live for the full document pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.finance_advisor import FinanceAdvisorAgent  # noqa: E402
from app.schemas.portfolio import PortfolioRunRequest  # noqa: E402
from app.services.financial_calendar import read_financial_calendar  # noqa: E402
from app.services.financial_conflict import classify_portfolio_conflicts  # noqa: E402
from app.services.financial_retrieval import FinancialRetrieval  # noqa: E402
from app.services.llm_controller import LLMError, LLMSettings, RunModel  # noqa: E402
from app.services.service_policy import build_service_cards, get_answer_slots  # noqa: E402
from scripts.run_example import RecordedLiveModel, load_example  # noqa: E402


def fixture_request() -> PortfolioRunRequest:
    example = load_example()
    assert example.financial_calendar_path and example.transaction_id
    calendar = read_financial_calendar(example.financial_calendar_path, example.transaction_id)
    expected = json.loads((ROOT / "data/fixtures/tradeflow_example/expected.json").read_text())
    return PortfolioRunRequest(
        trade_direction="EXPORT",
        receipt_currency="USD",
        export_receivable_confirmed=True,
        transaction_id=example.transaction_id,
        expected_receipt_date=date.fromisoformat(expected["expected_receipt_date"]),
        financial_events=calendar.events,
    )


def write_review(payload: dict[str, Any], target: Path) -> None:
    """Private local audit, not a publicly redistributable bank-source document."""
    lines = [
        "# 충돌별 실제 RAG 쿼리와 답변",
        "",
        f"실행: {payload['run_id']} / 모드: {payload['mode']}",
        "",
        (
            "PDF/OCR·금융일정 Excel을 함께 처리한 전체 실제 실행 기록입니다."
            if payload["mode"] == "live_full_pipeline"
            else "합성 거래의 예상 입금일을 입력한 RAG 단독 검증입니다. OCR 검증과 구분합니다."
        ),
        "자동 검사는 출처 연결·원문 일치·검색 후보를 확인하며 금융 의미의 정확성을 보증하지 않습니다.",
        "은행 원문이 포함된 로컬 검토 기록입니다. 원문·인용 재배포 조건을 확인하기 전 공개하지 마세요.",
        "",
    ]
    for conflict in payload["conflicts"]:
        event_id = conflict["event_id"]
        lines.extend([f"## {event_id}: {conflict['event_name']}", "", conflict["reason"], ""])
        for trace in payload["retrieval_trace"]:
            if trace["event_id"] != event_id:
                continue
            lines.extend(
                [
                    f"### 검색: {trace['query_id']}",
                    "",
                    trace["query"],
                    "",
                    f"상태: {trace['status']} / 후보 본문: {trace.get('unique_candidate_count', '미측정')}개",
                    "",
                ]
            )
            for hit in trace["results"]:
                lines.extend(
                    [
                        f"- {hit['source_file']} p.{hit['page']} / score={hit.get('score')}",
                        f"  - {hit['excerpt']}",
                    ]
                )
            lines.append("")
        lines.extend(["### 실제 LLM 설명", ""])
        options = [o for o in payload.get("product_options", []) if o["event_id"] == event_id]
        if not options:
            lines.extend(["생성하지 않음: 검색 전용 실행 또는 근거 부족·오류 상태.", ""])
        for option in options:
            lines.extend([f"**{option['product_name']} — {option['financial_institution']}**", ""])
            for point in option["explanation_points"]:
                lines.extend(
                    [
                        f"질문: {point.get('question') or '-'}",
                        "",
                        point["text"],
                        "",
                        f"> {point['supporting_quote']}",
                        "",
                    ]
                )
                lines.extend(
                    [f"출처: {c['source_file']} p.{c['page']}" for c in point["citations"]]
                )
                lines.append("")
    lines.extend(["## 자동 확인", ""])
    lines.extend([f"- {name}: {passed}" for name, passed in payload["checks"].items()])
    lines.extend(["", "## 경고", "", *payload["warnings"], ""])
    target.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Paid, at most thirteen gpt-5-nano Flex calls."
    )
    parser.add_argument("--output-directory", type=Path, default=ROOT / "output/rag-verification")
    parser.add_argument(
        "--from-result",
        type=Path,
        help="Render an existing run_example result as a local query/answer review; no API call.",
    )
    args = parser.parse_args()
    if args.from_result:
        if args.live:
            parser.error("--from-result and --live cannot be combined")
        full = json.loads(args.from_result.read_text(encoding="utf-8"))
        verification = full["verification"]
        if not verification.get("live_llm_executed"):
            parser.error("--from-result requires an actual live pipeline result")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(verification.get("run_id", ""))):
            parser.error("--from-result requires a safe run identifier")
        review = {
            **full,
            "run_id": verification["run_id"],
            "mode": "live_full_pipeline",
            "checks": verification["checks"],
        }
        args.output_directory.mkdir(parents=True, exist_ok=True)
        target = args.output_directory / f"{verification['run_id']}-full-review.md"
        write_review(review, target)
        print(f"Existing-run review (no API calls): {target}")
        return 0
    settings = LLMSettings.from_env().model_copy(
        update={
            "model": "gpt-5-nano",
            "service_tier": "flex",
            "max_calls": 13,
            "max_output_tokens": 6000,
            "timeout_seconds": 180,
        }
    )
    if args.live and not os.getenv("OPENAI_API_KEY", "").strip():
        print("OPENAI_API_KEY가 설정되지 않았습니다. 키 값은 출력하지 않습니다.")
        return 2
    args.output_directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    request, retrieval = fixture_request(), FinancialRetrieval()
    conflicts = classify_portfolio_conflicts(
        request.expected_receipt_date,
        request.financial_events,
        receipt_currency=request.receipt_currency,
    )
    options, warnings = [], []
    runtime = None
    error = None
    if args.live:
        model = RecordedLiveModel(settings, args.output_directory / f"{run_id}-calls.json")
        runtime = RunModel(model, 13)
        try:
            conflicts, options, warnings = FinanceAdvisorAgent(retrieval=retrieval).run(
                request,
                [],
                model=runtime,
            )
        except LLMError as exc:
            error = str(exc)
            warnings.append(error)
    else:
        _, warnings = retrieval.search(request, conflicts)
    trace = retrieval.last_search_trace
    searched = [t for t in trace if t["status"] == "SEARCHED"]
    checks = {
        "three_conflicts": len(conflicts) == 3 and all(c.status == "CONFLICT" for c in conflicts),
        "all_questions_have_multiple_candidates": bool(searched)
        and all(t.get("unique_candidate_count", 0) > 1 for t in searched),
        "no_runtime_page_or_topic_filter": bool(searched)
        and all(t["page_filter"] is None and t["topic_filter"] is None for t in searched),
        "all_queries_retrieved_evidence": bool(searched) and all(t["results"] for t in searched),
        "no_archived_bill_cost_source": all(
            h["source_file"] != "hsbc_trade_finance.pdf" for t in trace for h in t["results"]
        ),
    }
    if args.live:
        checks.update(
            {
                "all_conflicts_explained": {o.event_id for o in options}
                == {c.event_id for c in conflicts},
                "all_points_have_exact_source_quotes": bool(options)
                and all(
                    p.supporting_quote in c.excerpt
                    for o in options
                    for p in o.explanation_points
                    for c in p.citations
                ),
                "all_options_have_generated_explanations": bool(options)
                and all(o.explanation_points for o in options),
                "all_key_questions_answered": {
                    (o.event_id, p.slot_id) for o in options for p in o.explanation_points
                }
                == {
                    (c.event_id, slot.slot_id)
                    for c in conflicts
                    if c.status == "CONFLICT"
                    for slot in get_answer_slots(
                        c.scenario_code,
                        export_receivable_confirmed=request.trade_direction == "EXPORT"
                        and request.export_receivable_confirmed,
                    )
                },
                "no_llm_failure": error is None
                and runtime is not None
                and runtime.calls == len(model.records),
            }
        )
    payload = {
        "run_id": run_id,
        "mode": "live_finance_agent" if args.live else "local_retrieval_only",
        "live_llm_executed": args.live,
        "model": settings.model if args.live else None,
        "requested_service_tier": settings.service_tier if args.live else None,
        "llm_calls": runtime.calls if runtime else 0,
        "input_tokens": runtime.input_tokens if runtime else 0,
        "output_tokens": runtime.output_tokens if runtime else 0,
        "tool_audit": [a.model_dump(mode="json") for a in runtime.audit] if runtime else [],
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
        "product_options": [o.model_dump(mode="json") for o in options],
        "service_cards": [
            c.model_dump(mode="json") for c in build_service_cards(conflicts, options)
        ],
        "retrieval_trace": trace,
        "checks": checks,
        "warnings": warnings,
    }
    if args.live:
        payload["served_service_tiers"] = sorted(
            {r.get("served_service_tier") or "unknown" for r in model.records}
        )
        payload["estimated_usd_standard_upper"] = round(
            (payload["input_tokens"] * 0.05 + payload["output_tokens"] * 0.40) / 1_000_000, 8
        )
        payload["cost_note"] = (
            "기록된 토큰의 표준 단가 추정이며 청구액이 아닙니다. 실패 호출의 미보고 사용량은 제외됩니다."
        )
    stem = args.output_directory / run_id
    stem.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_review(payload, stem.with_suffix(".md"))
    print(
        json.dumps(
            {
                k: payload[k]
                for k in [
                    "run_id",
                    "mode",
                    "llm_calls",
                    "input_tokens",
                    "output_tokens",
                    "checks",
                    "warnings",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Review: {stem.with_suffix('.md')}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
