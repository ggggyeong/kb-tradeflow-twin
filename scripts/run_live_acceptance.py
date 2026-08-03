from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import PROJECT_ROOT
from app.db.models import (
    CalculationResult,
    Conflict,
    DocumentFieldOverride,
    FinancialEvent,
    Report,
    TradeCase,
)
from app.db.session import session_scope
from app.services.ingestion.batch_service import upload_batch_directory

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "live_acceptance"
DEFAULT_REPORT = PROJECT_ROOT / "LIVE_4_WORKFLOW_CHAT_INTERACT_LOG.md"
FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"
FINANCIAL_WORKBOOK = (
    PROJECT_ROOT / "data" / "demo_judges_v2" / "KB_TradeFlow_금융일정_2Sheet_데모예시.xlsx"
)


@dataclass(frozen=True)
class ChatRun:
    label: str
    question: str
    hints: dict[str, Any]
    human_answers: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_document_upload(batch_id: str) -> list[Path]:
    """Stage the eight real fixture PDFs exactly as an upload batch."""
    manifest_path = FIXTURE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_paths = sorted(FIXTURE_DIR.glob("*.pdf"))
    if len(source_paths) != 8:
        raise AssertionError(f"Expected 8 fixture PDFs, found {len(source_paths)}")
    expected = {item["file_name"]: item["sha256"] for item in manifest["documents"]}
    target = upload_batch_directory(batch_id)
    target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in source_paths:
        source_hash = _sha256(source)
        if expected.get(source.name) != source_hash:
            raise AssertionError(f"Fixture manifest mismatch: {source.name}")
        destination = target / source.name
        if destination.exists() and _sha256(destination) != source_hash:
            raise FileExistsError(f"Upload target contains a different file: {destination}")
        if not destination.exists():
            shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def _get(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _run_error(client: httpx.Client, thread_id: str) -> str:
    state = _get(client, f"/threads/{thread_id}/state")
    errors = [str(task.get("error")) for task in state.get("tasks", []) if task.get("error")]
    return " | ".join(errors) or "Agent Server returned no task error"


def _assert_state(
    client: httpx.Client,
    thread_id: str,
    state: Any,
    phase: str,
) -> dict[str, Any]:
    if not isinstance(state, dict) or not state:
        raise RuntimeError(
            f"Thread {thread_id} failed during {phase}: {_run_error(client, thread_id)}"
        )
    return state


def _node_trace(client: httpx.Client, thread_id: str) -> list[str]:
    history = _post(client, f"/threads/{thread_id}/history", {"limit": 1000})
    nodes: list[str] = []
    for checkpoint in reversed(history):
        for task in checkpoint.get("tasks", []):
            node = str(task.get("name", ""))
            if not node or node.startswith("__"):
                continue
            if not nodes or nodes[-1] != node:
                nodes.append(node)
    return nodes


def _interrupt_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = state.get("__interrupt__", [])
    if not interrupts:
        return None
    value = interrupts[0].get("value", {})
    return value if isinstance(value, dict) else {"value": value}


def _issue(interrupt_payload: dict[str, Any]) -> dict[str, Any]:
    issue = interrupt_payload.get("issue", interrupt_payload)
    if not isinstance(issue, dict) or not issue.get("issue_code"):
        raise AssertionError(f"Malformed Human interrupt: {interrupt_payload}")
    return issue


def _assert_success(state: dict[str, Any], label: str) -> None:
    response = state.get("final_response", {})
    if response.get("status") != "SUCCESS":
        raise AssertionError(
            f"{label}: final status={response.get('status')}, reason={response.get('reason')}"
        )
    reviews = state.get("review_log", [])
    failed = [
        review
        for review in reviews
        if review.get("tool_status") != "SUCCESS" or review.get("decision") == "STOP"
    ]
    if failed:
        raise AssertionError(f"{label}: Supervisor review failed: {failed}")


def _run_chat(
    client: httpx.Client,
    spec: ChatRun,
    *,
    run_id: str,
) -> dict[str, Any]:
    thread_id = _post(client, "/threads", {})["thread_id"]
    input_state = {
        "messages": [{"role": "user", "content": spec.question}],
        "request_id": run_id,
        "thread_id": thread_id,
        "company_id": "DEMO-A",
        **spec.hints,
    }
    state = _post(
        client,
        f"/threads/{thread_id}/runs/wait",
        {"assistant_id": "chat", "input": input_state},
    )
    state = _assert_state(client, thread_id, state, f"{spec.label} initial run")
    interrupts: list[dict[str, Any]] = []
    for _ in range(8):
        payload = _interrupt_payload(state)
        if payload is None:
            break
        issue = _issue(payload)
        issue_code = str(issue["issue_code"])
        if issue_code in spec.human_answers:
            answer = spec.human_answers[issue_code]
        elif (
            issue_code.startswith(("FIELD-", "HREQ-"))
            or issue.get("exact_standard_field")
            or (
                isinstance(issue.get("context"), dict)
                and issue["context"].get("exact_standard_field")
            )
        ):
            answer = "2026-09-03"
        elif issue_code.startswith("PAYMENT_ANCHOR_REQUIRED:"):
            answer = "ON_BOARD_DATE"
        elif issue_code.startswith("FINANCIAL_LINK_SCOPE_REQUIRED:"):
            answer = "FULL"
        else:
            raise AssertionError(f"{spec.label}: no acceptance answer for {issue_code}")
        resume = {"issue_code": issue_code, "value": answer}
        interrupts.append(
            {
                "confirmation_id": payload.get("confirmation_id"),
                "task_id": payload.get("task_id"),
                "issue": issue,
                "resume": resume,
            }
        )
        state = _post(
            client,
            f"/threads/{thread_id}/runs/wait",
            {
                "assistant_id": "chat",
                "command": {"resume": resume},
            },
        )
        state = _assert_state(client, thread_id, state, f"{spec.label} resume")
    else:
        raise AssertionError(f"{spec.label}: more than 8 Human interrupts")
    if _interrupt_payload(state) is not None:
        raise AssertionError(f"{spec.label}: unresolved Human interrupt")
    _assert_success(state, spec.label)
    return {
        "label": spec.label,
        "question": spec.question,
        "hints": spec.hints,
        "thread_id": thread_id,
        "node_trace": _node_trace(client, thread_id),
        "interrupts": interrupts,
        "state": state,
    }


def _domain_evidence() -> dict[str, Any]:
    """Read final facts independently of model phrasing or generated task ids."""
    with session_scope() as session:
        cases = list(
            session.scalars(
                select(TradeCase)
                .where(TradeCase.case_id.in_(["TRD-001", "TRD-002", "TRD-003"]))
                .order_by(TradeCase.case_id)
            )
        )
        overrides = list(
            session.scalars(
                select(DocumentFieldOverride).where(
                    DocumentFieldOverride.exact_standard_field == "on_board_date"
                )
            )
        )
        events = list(session.scalars(select(FinancialEvent)))
        calculations = list(
            session.scalars(
                select(CalculationResult)
                .where(CalculationResult.case_id == "TRD-003")
                .order_by(CalculationResult.created_at)
            )
        )
        calculation_ids = [item.calculation_id for item in calculations]
        conflicts = (
            list(
                session.scalars(
                    select(Conflict).where(Conflict.calculation_id.in_(calculation_ids))
                )
            )
            if calculation_ids
            else []
        )
        reports = list(
            session.scalars(
                select(Report).where(Report.case_id == "TRD-003").order_by(Report.created_at)
            )
        )
    return {
        "cases": {
            case.case_id: {
                "status": case.status,
                "company_id": case.company_id,
                "transaction_id": case.transaction_id,
            }
            for case in cases
        },
        "on_board_date_overrides": [
            {
                "document_id": item.document_id,
                "raw_input": item.raw_input,
                "normalized": item.normalized_json,
                "evidence_source": item.evidence_source,
            }
            for item in overrides
        ],
        "financial_event_count": len(events),
        "trd_003_calculations": [
            {
                "calculation_id": item.calculation_id,
                "source_kind": item.source_kind,
                "as_of_date": item.as_of_date,
                "result": item.result_json,
            }
            for item in calculations
        ],
        "trd_003_conflicts": [
            {
                "financial_event_id": item.financial_event_id,
                "priority": item.priority,
                "gap_days": item.gap_days,
                "days_until_event": item.days_until_event,
                "reason": item.reason,
            }
            for item in conflicts
        ],
        "trd_003_reports": [
            {
                "report_id": item.report_id,
                "audience": item.audience,
                "basis_version": item.basis_version,
                "asset_path": item.asset_path,
            }
            for item in reports
        ],
    }


def _validate_domain(evidence: dict[str, Any]) -> None:
    cases = evidence["cases"]
    if set(cases) != {"TRD-001", "TRD-002", "TRD-003"}:
        raise AssertionError(f"Expected three trade cases, got {sorted(cases)}")
    if cases["TRD-001"]["status"] != "MONITORING_READY":
        raise AssertionError(f"TRD-001 status={cases['TRD-001']['status']}")
    if cases["TRD-002"]["status"] != "MONITORING_READY":
        raise AssertionError(f"TRD-002 status={cases['TRD-002']['status']}")
    if cases["TRD-003"]["status"] != "AWAITING_DOCUMENT":
        raise AssertionError(f"TRD-003 status={cases['TRD-003']['status']}")
    if not evidence["on_board_date_overrides"]:
        raise AssertionError("The exact BILL_OF_LADING.on_board_date Human override is missing")
    if evidence["financial_event_count"] == 0:
        raise AssertionError("No financial events were imported")
    if not evidence["trd_003_calculations"]:
        raise AssertionError("No TRD-003 risk calculations were persisted")
    if not evidence["trd_003_conflicts"]:
        raise AssertionError("No TRD-003 conflicts were persisted")
    audiences = {item["audience"] for item in evidence["trd_003_reports"]}
    if not {"CUSTOMER", "RM"} <= audiences:
        raise AssertionError(f"Expected CUSTOMER and RM reports, got {audiences}")
    report_bases = {
        str(item["basis_version"])
        for item in evidence["trd_003_reports"]
        if item.get("audience") in {"CUSTOMER", "RM"}
    }
    if len(report_bases) != 1:
        raise AssertionError(
            "CUSTOMER and RM reports must share exactly one frozen risk basis, "
            f"got {sorted(report_bases)}"
        )
    for item in evidence["trd_003_reports"]:
        if not Path(item["asset_path"]).is_file():
            raise AssertionError(f"Report asset is missing: {item['asset_path']}")


def _validate_workflow_records(records: list[dict[str, Any]]) -> None:
    """Assert workflow-specific evidence beyond each graph's final SUCCESS."""
    if len(records) != 5:
        raise AssertionError(f"Expected five live graph runs for four demos, got {len(records)}")
    expected_tools = [
        {
            "stage_document_intelligence",
            "bundle_trade_cases",
            "commit_trade_cases",
            "evidence_review",
        },
        {
            "validate_financial_calendar",
            "import_financial_calendar",
            "evidence_review",
        },
        {
            "select_monitoring_candidates",
            "evidence_review",
        },
        {
            "record_delay",
            "inspect_payment_gate",
            "calculate_financial_exposure",
            "evidence_review",
        },
        {
            "match_product_scenario",
            "retrieve_product_evidence",
            "build_briefing_payload",
            "render_customer_report",
            "render_rm_report",
            "evidence_review",
        },
    ]
    for record, required in zip(records, expected_tools, strict=True):
        actual = set(record.get("state", {}).get("task_results", {}))
        missing = required - actual
        if missing:
            raise AssertionError(f"{record.get('label')}: missing Tool results {sorted(missing)}")

    product_result = records[-1]["state"]["task_results"]["retrieve_product_evidence"]
    options = product_result.get("options", [])
    if not options:
        raise AssertionError("Product workflow returned no grounded KB options")
    for option in options:
        citations = option.get("citations", [])
        if not citations or any(not item.get("page") for item in citations):
            raise AssertionError(
                f"Product option has no source-page citation: {option.get('product_name')}"
            )


def _short_result(value: Any, limit: int = 300) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _render_report(
    *,
    server_url: str,
    batch_id: str,
    records: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> str:
    lines = [
        "# KB TradeFlow Twin — 4개 Live Workflow Chat / LangGraph Interact 기록",
        "",
        "## 실행 기준",
        "",
        f"- 실행 시각(UTC): `{datetime.now(UTC).isoformat()}`",
        f"- LangGraph Agent Server: `{server_url}`",
        "- Graph: `chat`",
        "- 모드: `live` (OpenAI structured Planning/Supervisor/Specialist)",
        "- 업무 숫자: 로컬 Tool, 실제 XLSX, DB 계산 결과만 사용",
        f"- 문서 upload batch: `{batch_id}` / 실제 PDF 8개",
        f"- 금융일정 원문: `{FINANCIAL_WORKBOOK.relative_to(PROJECT_ROOT)}`",
        "",
        "```text",
        "사용자 질문 → Planning Agent → Supervisor Agent",
        "→ 선택된 Specialist → 소유 Tool → Supervisor 수집",
        "→ Planning 재검토 → 필요 시 Human interrupt/resume",
        "→ Critic → Tool-grounded 최종 응답",
        "```",
        "",
    ]
    for number, record in enumerate(records, start=1):
        state = record["state"]
        plan = state.get("execution_plan", {})
        lines.extend(
            [
                f"## {number}. {record['label']}",
                "",
                "### 실제 Chat 입력",
                "",
                "```text",
                record["question"],
                "```",
                "",
                f"- Thread ID: `{record['thread_id']}`",
                f"- Node trace: `{' → '.join(record['node_trace'])}`",
                "",
                "### Planning 결과",
                "",
                "|순서|Task|Agent|Tool|공개 사유|",
                "|---:|---|---|---|---|",
            ]
        )
        for step in plan.get("steps", []):
            lines.append(
                "|{order}|`{task}`|`{agent}`|`{tool}`|{reason}|".format(
                    order=step.get("order"),
                    task=step.get("task_id"),
                    agent=step.get("agent"),
                    tool=step.get("tool"),
                    reason=str(step.get("reason", "")).replace("|", "/"),
                )
            )
        lines.extend(
            [
                "",
                "### Supervisor / Tool 실행",
                "",
                "|순서|Task|Agent|Tool|상태|",
                "|---:|---|---|---|---|",
            ]
        )
        reviews = {item.get("task_id"): item for item in state.get("review_log", [])}
        for item in state.get("execution_log", []):
            review = reviews.get(item.get("task_id"), {})
            lines.append(
                "|{sequence}|`{task}`|`{agent}`|`{tool}`|`{status}`|".format(
                    sequence=item.get("sequence"),
                    task=item.get("task_id"),
                    agent=item.get("agent"),
                    tool=item.get("tool"),
                    status=(
                        f"{review.get('tool_status', item.get('status'))}"
                        f" / {review.get('decision', '-')}"
                    ),
                )
            )
        if record["interrupts"]:
            lines.extend(["", "### Human interrupt / resume", ""])
            for item in record["interrupts"]:
                lines.append(
                    "- `{}` → `{}`".format(
                        item["issue"].get("issue_code"),
                        _short_result(item["resume"]),
                    )
                )
        lines.extend(
            [
                "",
                "### Tool 결과 키",
                "",
                f"`{list(state.get('task_results', {}))}`",
                "",
                "### 최종 Chat 응답",
                "",
                "```text",
                str(state.get("final_answer") or state.get("final_response", {}).get("answer", "")),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## DB 독립 검증",
            "",
            f"- 거래 상태: `{_short_result(evidence['cases'], 800)}`",
            f"- B/L 수기 보완: `{_short_result(evidence['on_board_date_overrides'], 800)}`",
            f"- 금융 이벤트 수: `{evidence['financial_event_count']}`",
            f"- TRD-003 계산 수: `{len(evidence['trd_003_calculations'])}`",
            f"- TRD-003 충돌 수: `{len(evidence['trd_003_conflicts'])}`",
            f"- 보고서: `{_short_result(evidence['trd_003_reports'], 1200)}`",
            "",
            "## 최종 판정",
            "",
            "4개 workflow는 별도의 demo 전용 graph 없이 동일한 `chat` graph와 "
            "Planning ↔ Supervisor 공통 제어를 사용했다. 문서 8개, 금융일정 XLSX, "
            "사용자 지연 제보, 상품 PDF 근거, 고객용·KB 직원용 보고서를 실제 Tool과 "
            "DB 결과로 검증했다.",
            "",
        ]
    )
    return "\n".join(lines)


def run(
    server_url: str,
    output_dir: Path,
    report_path: Path,
    *,
    resume_from: int = 1,
) -> None:
    if not 1 <= resume_from <= 6:
        raise ValueError("resume_from must be between 1 and 6")
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if resume_from == 1:
        batch_id = f"LIVE-8-PDF-{stamp}"
        uploaded = _prepare_document_upload(batch_id)
    else:
        for index in range(1, resume_from):
            record_path = output_dir / f"workflow_{index}.json"
            if not record_path.is_file():
                raise FileNotFoundError(
                    f"Cannot resume: prior acceptance record is missing: {record_path}"
                )
            records.append(json.loads(record_path.read_text(encoding="utf-8")))
        batch_id = str(records[0].get("hints", {}).get("batch_id") or "")
        if not batch_id:
            raise AssertionError("Cannot resume: workflow_1 record has no batch_id")
        uploaded = sorted(upload_batch_directory(batch_id).glob("*.pdf"))
        if len(uploaded) != 8:
            raise AssertionError(
                f"Cannot resume: expected 8 uploaded PDFs for {batch_id}, found {len(uploaded)}"
            )

    specs = [
        ChatRun(
            label="Workflow 1 — 8 PDF 분류·거래 매칭·누락 확인·DB 반영",
            question=(
                f"batch_id={batch_id}에 업로드한 8개 Booking, Commercial Invoice, "
                "Bill of Lading PDF를 분류하고 거래별로 묶어 누락을 확인한 뒤 "
                "수기 확인이 필요한 정확한 필드만 질문하고 DB에 반영해줘."
            ),
            hints={"workflow_kind": "UPLOAD_ANALYSIS", "batch_id": batch_id},
            human_answers={},
        ),
        ChatRun(
            label="Workflow 2-1 준비 — 실제 금융일정 XLSX 검증·반영",
            question=(
                "2-Sheet 금융일정 XLSX를 검증하고 로그인 회사의 기존 거래에 "
                "금융 이벤트와 연결 관계를 반영해줘."
            ),
            hints={
                "workflow_kind": "FINANCIAL_CALENDAR_IMPORT",
                "workbook_path": str(FINANCIAL_WORKBOOK),
            },
            human_answers={},
        ),
        ChatRun(
            label="Workflow 2-1 — 기준일 선제 모니터링",
            question=(
                "2026-09-18 기준으로 저장된 금융일정과 선적 상태를 선제 "
                "모니터링하고 위험 거래, 충돌 이유와 우선순위를 알려줘."
            ),
            hints={
                "workflow_kind": "PROACTIVE_MONITORING",
                "as_of_date": "2026-09-18",
            },
            human_answers={},
        ),
        ChatRun(
            label="Workflow 2-2 — 사용자 제보 선적 지연 영향",
            question=(
                "2026-08-20에 TRD-003 선적이 계획보다 9일 지연된다고 안내받았어. "
                "예상 수출대금 회수일과 금융일정 충돌, 발생 이유와 우선순위를 "
                "분석해줘."
            ),
            hints={
                "workflow_kind": "USER_REPORTED_DELAY",
                "case_ids": ["TRD-003"],
                "reported_at": "2026-08-20",
                "delay_days": [9],
            },
            human_answers={},
        ),
        ChatRun(
            label="Workflow 3 — KB 상품 근거와 고객/RM 브리핑",
            question=(
                "TRD-003의 B/L 지연으로 수출대금 회수가 늦어지는 상황에 활용할 "
                "수 있는 KB 상품을 원문 페이지 근거와 함께 찾고, 고객용과 KB "
                "직원용 브리핑을 같은 risk snapshot으로 만들어줘."
            ),
            hints={
                "workflow_kind": "PRODUCT_ADVISORY_REPORT",
                "case_ids": ["TRD-003"],
                "customer_role": "EXPORTER",
                "borrower_type": "CORPORATION",
                "scenario_codes": ["SUPPLIER_PAYMENT"],
                "known_facts": [
                    "NON_LC_EXPORT_RECEIVABLE",
                    "EXPORT_RECEIVABLE_EXISTS",
                ],
                "as_of_date": "2026-09-18",
                "rm_inbox": "trade-rm@kb.example",
                "generate_report": True,
            },
            human_answers={"REPORT_GENERATION_CONSENT": True},
        ),
    ]

    with httpx.Client(base_url=server_url, timeout=900.0) as client:
        health = _get(client, "/ok")
        if health.get("ok") is not True:
            raise RuntimeError(f"LangGraph Agent Server is not ready: {health}")
        for index, spec in enumerate(specs, start=1):
            if index < resume_from:
                continue
            print(f"[{index}/{len(specs)}] {spec.label}", flush=True)
            record = _run_chat(
                client,
                spec,
                run_id=f"ACCEPT-{stamp}-{index}",
            )
            records.append(record)
            (output_dir / f"workflow_{index}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    evidence = _domain_evidence()
    _validate_workflow_records(records)
    _validate_domain(evidence)
    evidence["uploaded_files"] = [{"path": str(path), "sha256": _sha256(path)} for path in uploaded]
    (output_dir / "domain_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report_path.write_text(
        _render_report(
            server_url=server_url,
            batch_id=batch_id,
            records=records,
            evidence=evidence,
        ),
        encoding="utf-8",
    )
    print(f"[완료] {report_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all live TradeFlow workflows against LangGraph Agent Server"
    )
    parser.add_argument("--server-url", default="http://127.0.0.1:2024")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--resume-from",
        type=int,
        default=1,
        help=(
            "Resume at workflow record 1..5, or use 6 to validate/finalize all "
            "existing records without another live graph run."
        ),
    )
    args = parser.parse_args()
    run(
        args.server_url.rstrip("/"),
        args.output_dir,
        args.report,
        resume_from=args.resume_from,
    )


if __name__ == "__main__":
    main()
