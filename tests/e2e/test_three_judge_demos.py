from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pypdf import PdfReader
from sqlalchemy import func, select

import app.services.ingestion.batch_service as batch_module
import app.services.reports as reports_module
from app.core.config import PROJECT_ROOT
from app.db.models import Alert, CalculationResult, Conflict, MonitoringRun, Report, TradeCase
from app.db.session import session_scope
from app.graphs.compiled import build_root_conversation_graph
from scripts.prepare_final_demo_portfolio import prepare_final_demo_portfolio

FINAL_DEMOS = PROJECT_ROOT / "data" / "judge_demo_final"


def _pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _stage_demo_zero(batch_id: str, upload_root: Path) -> None:
    target = upload_root / batch_id
    target.mkdir(parents=True)
    source = FINAL_DEMOS / "demo_0_complete_batch"
    for path in sorted(source.glob("*.pdf")):
        shutil.copy2(path, target / path.name)


def _issue(state: dict) -> dict | None:
    interrupts = state.get("__interrupt__", [])
    if not interrupts:
        return None
    item = interrupts[0]
    payload = item.value if hasattr(item, "value") else item["value"]
    return payload.get("issue", payload)


def _answer(issue: dict) -> object:
    code = str(issue["issue_code"])
    if code.startswith("PAYMENT_ANCHOR_REQUIRED:"):
        return "ON_BOARD_DATE"
    if code.startswith("FINANCIAL_LINK_SCOPE_REQUIRED:"):
        return "FULL"
    if code == "REPORT_GENERATION_CONSENT":
        return True
    if code.startswith("PRODUCT_BORROWER_TYPE_REQUIRED:"):
        return "CORPORATION"
    if code.startswith("PRODUCT_REQUIREMENT:"):
        return False
    raise AssertionError(f"No test answer is defined for {code}")


def _run_to_end(graph, graph_input: dict, *, thread_id: str) -> tuple[dict, list[str]]:
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.invoke(graph_input, config=config)
    issue_codes: list[str] = []
    for _ in range(12):
        issue = _issue(state)
        if issue is None:
            break
        issue_codes.append(str(issue["issue_code"]))
        state = graph.invoke(
            Command(
                resume={
                    "issue_code": issue["issue_code"],
                    "value": _answer(issue),
                }
            ),
            config=config,
        )
    else:
        raise AssertionError("Too many Human interrupts")
    assert _issue(state) is None
    assert state["final_response"]["status"] == "SUCCESS"
    return state, issue_codes


def _new_request(*, thread_id: str, request_id: str, message: str, **hints) -> dict:
    return {
        "planning_initialized": False,
        "messages": [{"role": "user", "content": message}],
        "request_id": request_id,
        "thread_id": thread_id,
        **hints,
    }


def test_final_demo_zero_one_two_execute_through_chat_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(batch_module, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(batch_module, "OBJECT_DIR", tmp_path / "objects")
    monkeypatch.setattr(reports_module, "REPORT_DIR", tmp_path / "reports")

    # Demo 1 and 2 deliberately start with Booking + Invoice, an absent B/L,
    # and already imported financial schedules.  The preparation is model-free.
    prepared = prepare_final_demo_portfolio(("1", "2"))
    assert prepared["model_api_calls"] == 0

    graph = build_root_conversation_graph(InMemorySaver())

    # Demo 0 — eight mixed documents are classified into three case bundles.
    demo0_batch = "FINAL-E2E-DEMO0"
    _stage_demo_zero(demo0_batch, uploads)
    demo0, demo0_issues = _run_to_end(
        graph,
        _new_request(
            thread_id="final-demo-0-thread",
            request_id="final-demo-0",
            message="3",
            company_id="DEMO0-CO",
            workflow_kind="UPLOAD_ANALYSIS",
            batch_id=demo0_batch,
        ),
        thread_id="final-demo-0-thread",
    )
    assert demo0_issues == []
    assert demo0["final_response"]["results"]["commit_trade_cases"][
        "committed_case_ids"
    ] == ["CASE-DEMO0", "CASE-DEMO0B", "CASE-DEMO0C"]
    demo0_answer = demo0["final_response"]["answer"]
    for expected in (
        "CASE-DEMO0: Booking Confirmation 1개, Commercial Invoice 1개, B/L 1개",
        "CASE-DEMO0B: Booking Confirmation 1개, Commercial Invoice 1개, B/L 1개",
        "CASE-DEMO0C: Booking Confirmation 1개, Commercial Invoice 1개, B/L 0개",
    ):
        assert expected in demo0_answer
    assert "독립 Critic 판정" not in demo0_answer
    assert demo0["final_response"]["next_actions"][0]["id"] == (
        "register_financial_calendar"
    )

    # Demo 1 — manual "today" monitoring finds the missing-B/L frontier risk,
    # persists alerts and produces the internal monitoring PDF.
    demo1, demo1_issues = _run_to_end(
        graph,
        _new_request(
            thread_id="final-demo-1-thread",
            request_id="final-demo-1-risk",
            message="2",
            company_id="DEMO1-CO",
            workflow_kind="PROACTIVE_MONITORING",
            as_of_date="2026-08-20",
        ),
        thread_id="final-demo-1-thread",
    )
    assert demo1_issues == []
    finalized = demo1["final_response"]["results"]["finalize_manual_monitoring"]
    assert finalized["priority_summary"]["ordered_case_ids"] == ["CASE-DEMO1"]
    assert Path(finalized["daily_report"]["asset_path"]).is_file()
    assert demo1["final_response"]["next_actions"][0]["id"] == "build_conflict_report"

    demo1_report, consent_issues = _run_to_end(
        graph,
        _new_request(
            thread_id="final-demo-1-thread",
            request_id="final-demo-1-report",
            message="CASE-DEMO1의 고객용·RM용 충돌 종합보고서를 만들어줘.",
            company_id="DEMO1-CO",
            case_ids=["CASE-DEMO1"],
            workflow_kind="PRODUCT_ADVISORY_REPORT",
            generate_report=True,
            as_of_date="2026-08-20",
        ),
        thread_id="final-demo-1-thread",
    )
    assert "REPORT_GENERATION_CONSENT" in consent_issues
    demo1_customer_path = Path(
        demo1_report["final_response"]["results"]["render_customer_report"]["asset_path"]
    )
    demo1_rm_path = Path(
        demo1_report["final_response"]["results"]["render_rm_report"]["asset_path"]
    )
    assert demo1_customer_path.is_file()
    assert demo1_rm_path.is_file()

    demo1_customer_text = _pdf_text(demo1_customer_path)
    demo1_rm_text = _pdf_text(demo1_rm_path)
    for expected in (
        "2026-10-04",
        "2026-10-10",
        "2026-08-11",
        "9일",
        "아직 확정 연체가 아닙니다",
        "B/L 미수령",
    ):
        assert expected in demo1_customer_text
    for expected in (
        "CASE-DEMO1",
        "TXN-DEMO1",
        "ON_BOARD_DATE",
        "CALENDAR",
        "P4",
        "상담 준비 체크리스트",
    ):
        assert expected in demo1_rm_text

    # Demo 2 — a reported 9-day delay is stored as a scenario.  The ambiguous
    # B/L anchor and transaction dependency are resolved by typed Human resume.
    demo2, demo2_issues = _run_to_end(
        graph,
        _new_request(
            thread_id="final-demo-2-thread",
            request_id="final-demo-2-delay",
            message=(
                "CASE-DEMO2 거래의 B/L이 계획보다 9일 늦어진다는 소식을 들었어. "
                "금융일정 충돌과 위험순위를 분석해줘."
            ),
            company_id="DEMO2-CO",
            case_ids=["CASE-DEMO2"],
            workflow_kind="USER_REPORTED_DELAY",
            reported_at="2026-08-20",
            delay_days=[9],
        ),
        thread_id="final-demo-2-thread",
    )
    assert "PAYMENT_ANCHOR_REQUIRED:CASE-DEMO2" in demo2_issues
    assert any(code.startswith("FINANCIAL_LINK_SCOPE_REQUIRED:") for code in demo2_issues)
    exposure = demo2["final_response"]["results"]
    recalculations = [
        value
        for key, value in exposure.items()
        if key.startswith("recalculate_financial_exposure_")
    ]
    assert recalculations and recalculations[-1]["conflict_count"] >= 1
    assert demo2["final_response"]["next_actions"][0]["id"] == "build_conflict_report"

    demo2_report, demo2_report_issues = _run_to_end(
        graph,
        _new_request(
            thread_id="final-demo-2-thread",
            request_id="final-demo-2-report",
            message="CASE-DEMO2의 고객용·RM용 충돌 종합보고서를 만들어줘.",
            company_id="DEMO2-CO",
            case_ids=["CASE-DEMO2"],
            workflow_kind="PRODUCT_ADVISORY_REPORT",
            generate_report=True,
            as_of_date="2026-08-20",
        ),
        thread_id="final-demo-2-thread",
    )
    assert "REPORT_GENERATION_CONSENT" in demo2_report_issues
    customer = demo2_report["final_response"]["results"]["render_customer_report"]
    rm = demo2_report["final_response"]["results"]["render_rm_report"]
    assert customer["basis_version"] == rm["basis_version"]
    assert Path(customer["asset_path"]).is_file()
    assert Path(rm["asset_path"]).is_file()

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(MonitoringRun)) >= 1
        assert session.scalar(select(func.count()).select_from(Alert)) >= 1
        assert session.scalar(select(func.count()).select_from(CalculationResult)) >= 3
        assert session.scalar(select(func.count()).select_from(Conflict)) >= 2
        assert session.scalar(select(func.count()).select_from(Report)) >= 4
        demo1_case = session.get(TradeCase, "CASE-DEMO1")
        demo2_case = session.get(TradeCase, "CASE-DEMO2")
        assert demo1_case is not None and demo1_case.status == "AWAITING_DOCUMENT"
        assert demo2_case is not None and demo2_case.status == "AWAITING_DOCUMENT"
