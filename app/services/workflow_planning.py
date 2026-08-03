from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.schemas.planning import (
    ConversationalResponse,
    HumanIssue,
    Plan,
    PlanningResponse,
    PlanStep,
    WorkflowKind,
)

_CASE_PATTERN = re.compile(
    (
        r"(?<![A-Z0-9_-])"
        # TXN-* is the finance transaction_id, not the TradeCase case_id
        # consumed by Shipment and Financial Exposure tools.  Keep the two
        # identities separate and ask for case_id when only TXN-* is supplied.
        r"(?:CASE|TRD)(?:-[A-Z0-9][A-Z0-9_-]*|[0-9][A-Z0-9_-]*)"
        r"(?![A-Z0-9_-])"
    ),
    re.IGNORECASE,
)
_BATCH_PATTERN = re.compile(
    r"(?:batch(?:_id)?|배치)\s*[:=#]?\s*([A-Za-z0-9][A-Za-z0-9._-]*)",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_DELAY_PATTERN = re.compile(r"\+?\s*(\d{1,3})\s*일")

START_MENU_TEXT = """안녕하세요! KB TradeFlow Twin입니다.
원하시는 업무를 번호로 선택해 주세요.

1. 무역 TradeCase / Shipment 검색하기
2. 금일 금융위험 거래 분석하기
3. 새로운 무역파일 등록하기
4. 새로운 금융일정 등록하기"""

_MENU_REQUESTS = {
    "1": WorkflowKind.CASE_LOOKUP,
    "2": WorkflowKind.PROACTIVE_MONITORING,
    "3": WorkflowKind.UPLOAD_ANALYSIS,
    "4": WorkflowKind.FINANCIAL_CALENDAR_IMPORT,
}
_MENU_WORDS = {"메뉴", "시작", "처음", "도움말", "기능", "선택지"}
_GREETING_WORDS = {
    "안녕",
    "안녕하세요",
    "하이",
    "hello",
    "hi",
    "반가워",
    "반갑습니다",
}


def _contains(text: str, *terms: str) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _case_ids(text: str, hints: dict[str, Any]) -> list[str]:
    hinted = [str(item).strip() for item in hints.get("case_ids", []) if str(item).strip()]
    explicit = [match.group(0).upper() for match in _CASE_PATTERN.finditer(text)]
    return list(dict.fromkeys([*hinted, *explicit]))


def _batch_id(text: str, hints: dict[str, Any]) -> str | None:
    hinted = str(hints.get("batch_id") or "").strip()
    if hinted:
        return hinted
    match = _BATCH_PATTERN.search(text)
    return match.group(1) if match else None


def _as_of_date(text: str, hints: dict[str, Any]) -> str:
    hinted = hints.get("as_of_date")
    if hinted:
        return str(hinted)
    match = _DATE_PATTERN.search(text)
    if match:
        date.fromisoformat(match.group(1))
        return match.group(1)
    return str(hints.get("current_date") or date.today().isoformat())


def _delay_days(text: str, hints: dict[str, Any]) -> list[int]:
    hinted = hints.get("delay_days")
    if isinstance(hinted, list) and hinted:
        values = [int(item) for item in hinted]
    else:
        values = [int(match.group(1)) for match in _DELAY_PATTERN.finditer(text)]
    if any(value < 0 or value > 365 for value in values):
        raise ValueError("delay_days must be between 0 and 365")
    return list(dict.fromkeys(values))


def _critic_step(
    workflow_kind: WorkflowKind,
    evidence_task_ids: list[str],
) -> PlanStep:
    return PlanStep(
        task_id="evidence_review",
        description="Verify that every requested outcome is grounded in specialist Tool results",
        agent="critic",
        tool="review_workflow_evidence",
        args={
            "workflow_kind": workflow_kind.value,
            "expected_task_ids": evidence_task_ids,
            "evidence_snapshot": "$task_results",
        },
        reason="The independent Critic checks evidence presence and workflow safety contracts.",
    )


def _missing_value_step(
    *,
    issue_code: str,
    prompt: str,
    response_key: str,
    value_type: str = "string",
) -> PlanStep:
    return PlanStep(
        task_id=f"provide_{response_key}",
        description=f"Provide the required {response_key}",
        agent="human",
        tool="interrupt/resume",
        reason="A required workflow identifier was not present in the request.",
        human_issue=HumanIssue(
            issue_code=issue_code,
            prompt=prompt,
            response_key=response_key,
            value_type=value_type,  # type: ignore[arg-type]
        ),
    )


def _case_lookup_plan(text: str, hints: dict[str, Any]) -> Plan:
    """Read one TradeCase's shipment and latest finance-owned risk snapshot."""
    case_ids = _case_ids(text, hints)
    case_value = case_ids[0] if case_ids else "$human.case_id"
    steps: list[PlanStep] = []
    if not case_ids:
        steps.append(
            _missing_value_step(
                issue_code="CASE_LOOKUP_ID_REQUIRED",
                prompt="조회할 TradeCase ID를 입력해 주세요 (예: TRD-003).",
                response_key="case_id",
            )
        )
    steps.extend(
        [
            PlanStep(
                task_id="read_shipment_snapshot",
                description="Read the persisted shipment facts without inferring departure",
                agent="shipment_timeline",
                tool="read_shipment_snapshot",
                args={"case_id": case_value},
                reason="Shipment Timeline owns the safe Booking and B/L state view.",
            ),
            PlanStep(
                task_id="get_financial_risk_snapshot",
                description="Read the latest immutable financial-risk snapshot",
                agent="financial_exposure",
                tool="get_financial_risk_snapshot",
                args={"case_id": case_value, "as_of_date": None},
                reason="Financial Exposure owns persisted due-date and conflict results.",
            ),
            _critic_step(
                WorkflowKind.CASE_LOOKUP,
                ["read_shipment_snapshot", "get_financial_risk_snapshot"],
            ),
        ]
    )
    return Plan(
        mission_type=WorkflowKind.CASE_LOOKUP.value,
        case_ids=case_ids,
        steps=steps,
    )


def _upload_plan(
    text: str,
    hints: dict[str, Any],
    *,
    force_commit: bool = False,
) -> Plan:
    batch_id = _batch_id(text, hints)
    batch_value = batch_id or "$human.batch_id"
    steps: list[PlanStep] = []
    if batch_id is None:
        steps.append(
            _missing_value_step(
                issue_code="UPLOAD_BATCH_ID_REQUIRED",
                prompt="분석할 업로드 batch_id를 입력해 주세요.",
                response_key="batch_id",
            )
        )
    steps.append(
        PlanStep(
            task_id="stage_document_intelligence",
            description="Classify, extract, and validate the staged upload documents",
            agent="document_intelligence",
            tool="stage_document_intelligence",
            args={"batch_id": batch_value},
            reason="Document Intelligence owns deterministic classification and extraction.",
        )
    )
    steps.append(
        PlanStep(
            task_id="bundle_trade_cases",
            description="Match staged documents and evaluate transaction completeness",
            agent="trade_case_manager",
            tool="bundle_trade_cases",
            args={"batch_id": batch_value},
            reason=(
                "Trade Case Manager owns transaction matching, completeness, and "
                "data-driven Human requests."
            ),
        )
    )
    evidence_ids = ["stage_document_intelligence", "bundle_trade_cases"]
    if force_commit or _contains(text, "커밋", "반영", "등록", "commit"):
        steps.append(
            PlanStep(
                task_id="commit_trade_cases",
                description="Commit the reviewed transaction shells and their explicit statuses",
                agent="trade_case_manager",
                tool="commit_trade_cases",
                args={"batch_id": batch_value},
                reason=(
                    "The Trade Case Manager commits after any Tool-returned exact-field "
                    "Human requests are resolved; whole-document gaps remain awaiting documents."
                ),
            )
        )
        evidence_ids.append("commit_trade_cases")
    steps.append(_critic_step(WorkflowKind.UPLOAD_ANALYSIS, evidence_ids))
    return Plan(
        mission_type=WorkflowKind.UPLOAD_ANALYSIS.value,
        case_ids=[],
        steps=steps,
    )


def _financial_calendar_plan(text: str, hints: dict[str, Any]) -> Plan:
    batch_id = _batch_id(text, hints)
    workbook_path = str(hints.get("workbook_path") or "").strip() or None
    company_id = str(hints.get("company_id") or "").strip() or None
    company_value = company_id or "$human.company_id"
    steps: list[PlanStep] = []
    if company_id is None:
        steps.append(
            _missing_value_step(
                issue_code="COMPANY_CONTEXT_REQUIRED",
                prompt="현재 상담 세션의 company_id를 입력해 주세요 (예: DEMO-A).",
                response_key="company_id",
            )
        )
    batch_value: str | None = batch_id
    if batch_id is None and workbook_path is None:
        steps.append(
            _missing_value_step(
                issue_code="FINANCIAL_CALENDAR_SOURCE_REQUIRED",
                prompt=(
                    "금융일정 XLSX가 포함된 upload batch_id를 입력해 주세요. "
                    "전체 워크북을 임의 생성하지 않습니다."
                ),
                response_key="batch_id",
            )
        )
        batch_value = "$human.batch_id"
    source_args = {
        "company_id": company_value,
        "workbook_path": workbook_path,
        "batch_id": batch_value,
    }
    calendar_steps = [
        PlanStep(
            task_id="validate_financial_calendar",
            description="Validate the uploaded two-sheet financial event and transaction-link contract",
            agent="financial_calendar",
            tool="validate_financial_calendar",
            args=source_args,
            reason=(
                "Financial Calendar validates the two source sheets and confirms that every "
                "transaction belongs to the authenticated company, without risk calculation."
            ),
        ),
        PlanStep(
            task_id="import_financial_calendar",
            description="Idempotently import financial events and their existing-transaction links",
            agent="financial_calendar",
            tool="import_financial_calendar",
            args=source_args,
            reason=(
                "Financial Calendar writes only FinancialEvent and transaction-event link facts; "
                "Company and TradeCase already exist and are not created from this workbook."
            ),
        ),
    ]
    evidence_ids = ["validate_financial_calendar", "import_financial_calendar"]
    if _contains(text, "충돌", "분석", "위험", "모니터링", "monitor", "risk"):
        as_of = _as_of_date(text, hints)
        calendar_steps.append(
            PlanStep(
                task_id="select_monitoring_candidates",
                description=f"Select imported transactions for financial-risk analysis as of {as_of}",
                agent="financial_calendar",
                tool="select_financial_monitoring_candidates",
                args={"company_id": company_value, "as_of_date": as_of, "limit": 100},
                reason=(
                    "After import, Financial Calendar selects candidates so the controller can "
                    "expand Shipment and Financial Exposure tasks."
                ),
            )
        )
        evidence_ids.append("select_monitoring_candidates")
    calendar_steps.append(_critic_step(WorkflowKind.FINANCIAL_CALENDAR_IMPORT, evidence_ids))
    steps.extend(calendar_steps)
    return Plan(
        mission_type=WorkflowKind.FINANCIAL_CALENDAR_IMPORT.value,
        case_ids=[],
        steps=steps,
    )


def _monitoring_plan(text: str, hints: dict[str, Any]) -> Plan:
    as_of = _as_of_date(text, hints)
    company_id = str(hints.get("company_id") or "").strip() or None
    company_value = company_id or "$human.company_id"
    steps: list[PlanStep] = []
    if company_id is None:
        steps.append(
            _missing_value_step(
                issue_code="COMPANY_CONTEXT_REQUIRED",
                prompt="금일 금융위험을 분석할 company_id를 입력해 주세요 (예: DEMO-A).",
                response_key="company_id",
            )
        )
    steps.extend(
        [
            PlanStep(
                task_id="select_monitoring_candidates",
                description=f"Select imported financial-calendar candidates as of {as_of}",
                agent="financial_calendar",
                tool="select_financial_monitoring_candidates",
                args={"company_id": company_value, "as_of_date": as_of, "limit": 100},
                reason=(
                    "Financial Calendar selects transaction candidates; the common controller "
                    "expands each into visible shipment and finance tasks."
                ),
            ),
            _critic_step(
                WorkflowKind.PROACTIVE_MONITORING,
                ["select_monitoring_candidates"],
            ),
            PlanStep(
                task_id="finalize_manual_monitoring",
                description=(
                    "Persist the reviewed manual monitoring run and render its internal report"
                ),
                agent="report_writer",
                tool="finalize_manual_monitoring",
                args={
                    "as_of_date": as_of,
                    "request_id": "${request_id}-manual-monitoring",
                    "evidence_snapshot": "$task_results",
                },
                reason=(
                    "Report Writer finalizes only the Critic-reviewed snapshots produced by the "
                    "manual chat request."
                ),
            ),
        ]
    )
    return Plan(
        mission_type=WorkflowKind.PROACTIVE_MONITORING.value,
        case_ids=_case_ids(text, hints),
        steps=steps,
    )


def _delay_plan(text: str, hints: dict[str, Any]) -> Plan:
    case_ids = _case_ids(text, hints)
    case_value = case_ids[0] if case_ids else "$human.case_id"
    reported_at = str(hints.get("reported_at") or _as_of_date(text, hints))
    delays = _delay_days(text, hints)
    delay_value: list[int] | str = delays or "$human.delay_days"
    steps: list[PlanStep] = []
    if not case_ids:
        steps.append(
            _missing_value_step(
                issue_code="DELAY_CASE_ID_REQUIRED",
                prompt="지연 제보를 연결할 case_id를 입력해 주세요.",
                response_key="case_id",
            )
        )
    if not delays:
        steps.append(
            _missing_value_step(
                issue_code="DELAY_DAYS_REQUIRED",
                prompt=("검토할 지연 일수 목록을 정수 배열로 입력해 주세요 (예: [5, 12])."),
                response_key="delay_days",
                value_type="integer_list",
            )
        )
    steps.extend(
        [
            PlanStep(
                task_id="record_delay",
                description="Record the user-reported delay and shipment-only scenarios",
                agent="shipment_timeline",
                tool="record_shipment_delay_scenarios",
                args={
                    "case_id": case_value,
                    "reported_at": reported_at,
                    "request_id": "${request_id}-shipment-delay",
                    "expected_delay_days": delay_value,
                },
                reason=(
                    "Shipment owns the reported event and conditional revised expected "
                    "departure dates."
                ),
            ),
            PlanStep(
                task_id="inspect_payment_gate",
                description="Inspect whether the payment calculation is currently permitted",
                agent="financial_exposure",
                tool="inspect_payment_gate",
                args={"case_id": case_value},
                reason="Finance must inspect the stored payment obligation before calculating.",
            ),
            PlanStep(
                task_id="calculate_financial_exposure",
                description="Calculate and persist finance-owned delay exposure",
                agent="financial_exposure",
                tool="calculate_financial_exposure",
                args={
                    "case_id": case_value,
                    "request_id": "${request_id}-financial-exposure",
                    "confirmed_anchor": None,
                },
                reason=(
                    "Finance calculates immediately when its gate is permitted; the "
                    "controller injects an exact anchor issue only when the gate requires it."
                ),
            ),
            _critic_step(
                WorkflowKind.USER_REPORTED_DELAY,
                [
                    "record_delay",
                    "inspect_payment_gate",
                    "calculate_financial_exposure",
                ],
            ),
        ]
    )
    return Plan(
        mission_type=WorkflowKind.USER_REPORTED_DELAY.value,
        case_ids=case_ids,
        steps=steps,
    )


def _product_plan(text: str, hints: dict[str, Any]) -> Plan:
    case_ids = _case_ids(text, hints)
    case_value = case_ids[0] if case_ids else "$human.case_id"
    wants_report = bool(hints.get("generate_report")) or _contains(
        text,
        "보고서",
        "브리핑",
        "rm",
        "handoff",
        "상담자료",
    )
    steps: list[PlanStep] = []
    financial_context: dict[str, Any] | str = {}
    if case_ids or wants_report:
        if not case_ids:
            steps.append(
                _missing_value_step(
                    issue_code="PRODUCT_CASE_ID_REQUIRED",
                    prompt="금융위험과 상품을 연결할 case_id를 입력해 주세요.",
                    response_key="case_id",
                )
            )
        steps.append(
            PlanStep(
                task_id="product_risk_snapshot",
                description="Load the latest persisted Financial Exposure result for the Case",
                agent="financial_exposure",
                tool="get_financial_risk_snapshot",
                args={
                    "case_id": case_value,
                    "as_of_date": hints.get("as_of_date"),
                    "allow_missing": True,
                },
                reason=(
                    "Product Advisor must receive the prior Financial Exposure result instead "
                    "of asking the customer to restate the situation."
                ),
            )
        )
        financial_context = "$result.product_risk_snapshot"
    steps.extend(
        [
            PlanStep(
                task_id="match_product_scenario",
                description=(
                    "Generate product candidates from the financial-risk result, DB profile, "
                    "document role, and hard filters"
                ),
                agent="product_advisor",
                tool="match_product_scenario",
                args={
                    "query": text,
                    "scenario_codes": hints.get("scenario_codes"),
                    "customer_role": hints.get("customer_role", "UNKNOWN"),
                    "borrower_type": hints.get("borrower_type", "UNKNOWN"),
                    "known_facts": hints.get("known_facts", []),
                    "not_met_facts": hints.get("not_met_facts", []),
                    "case_id": case_value if case_ids or wants_report else None,
                    "company_id": hints.get("company_id"),
                    "financial_context": financial_context,
                    "requirements_reviewed": False,
                },
                reason=(
                    "Product Advisor automatically converts the stored finance result into "
                    "search context and applies structured, auditable filters."
                ),
            ),
            PlanStep(
                task_id="retrieve_product_evidence",
                description=(
                    "Retrieve page-level OCR evidence and compose only citation-grounded options"
                ),
                agent="product_advisor",
                tool="retrieve_product_evidence",
                args={
                    "query": "$result.match_product_scenario.retrieval_query",
                    "product_ids": "$result.match_product_scenario.product_ids",
                    "matched_products": "$result.match_product_scenario.products",
                    "top_k_per_product": 2,
                },
                reason=(
                    "Product facts and presented options must come from hash-validated local PDF "
                    "pages; ungrounded candidates are excluded in this same Tool."
                ),
            ),
        ]
    )
    evidence_ids = ["match_product_scenario", "retrieve_product_evidence"]
    if case_ids or wants_report:
        evidence_ids.insert(0, "product_risk_snapshot")
    if wants_report:
        steps.extend(
            [
                PlanStep(
                    task_id="confirm_report_consent",
                    description="Capture explicit consent for customer and RM report generation",
                    agent="human",
                    tool="interrupt/resume",
                    reason="Customer and RM artifacts may only be generated after explicit consent.",
                    human_issue=HumanIssue(
                        issue_code="REPORT_GENERATION_CONSENT",
                        prompt=(
                            "동일한 risk snapshot으로 고객용·KB 직원용 브리핑을 "
                            "생성하는 데 동의합니까?"
                        ),
                        response_key="report_consent",
                        value_type="boolean",
                        allowed_values=[True, False],
                        cancel_values=[False],
                        context={"case_id": case_value},
                    ),
                ),
                PlanStep(
                    task_id="build_briefing_payload",
                    description="Freeze the workbook layout and shared risk snapshot",
                    agent="report_writer",
                    tool="build_briefing_payload",
                    args={
                        "case_id": case_value,
                        "product_options": "$result.retrieve_product_evidence.options",
                        "as_of_date": hints.get("as_of_date"),
                        "rm_inbox": hints.get("rm_inbox"),
                    },
                    reason=("Both audiences must use one frozen workbook/risk/product payload."),
                ),
                PlanStep(
                    task_id="render_customer_report",
                    description="Render the customer workbook blocks",
                    agent="report_writer",
                    tool="render_customer_report",
                    args={
                        "briefing_payload": "$result.build_briefing_payload",
                        "consent": "$human.report_consent",
                    },
                    reason="Customer content excludes internal priority and routing details.",
                ),
                PlanStep(
                    task_id="render_rm_report",
                    description="Render the KB employee workbook blocks",
                    agent="report_writer",
                    tool="render_rm_report",
                    args={
                        "briefing_payload": "$result.build_briefing_payload",
                        "consent": "$human.report_consent",
                    },
                    reason="RM content adds audit and routing fields over the identical basis.",
                ),
            ]
        )
        evidence_ids.extend(["render_customer_report", "render_rm_report"])
    steps.append(_critic_step(WorkflowKind.PRODUCT_ADVISORY_REPORT, evidence_ids))
    return Plan(
        mission_type=WorkflowKind.PRODUCT_ADVISORY_REPORT.value,
        case_ids=case_ids,
        steps=steps,
    )


def make_workflow_contract_decision(
    *,
    user_query: str,
    hints: dict[str, Any],
) -> PlanningResponse:
    """Create the typed execution contract used to validate a Planning proposal."""
    stripped = user_query.strip()
    normalized = stripped.casefold().replace(" ", "")
    if normalized in _GREETING_WORDS or normalized in _MENU_WORDS:
        return PlanningResponse(final_output=ConversationalResponse(response=START_MENU_TEXT))

    menu_kind = _MENU_REQUESTS.get(normalized)

    hinted_kind: WorkflowKind | None = None
    hinted_value = str(hints.get("workflow_kind") or "").strip()
    if hinted_value:
        try:
            hinted_kind = WorkflowKind(hinted_value)
        except ValueError as exc:
            supported = ", ".join(item.value for item in WorkflowKind)
            raise ValueError(
                f"Unsupported workflow_kind: {hinted_value}. Supported values: {supported}"
            ) from exc

    if menu_kind is WorkflowKind.CASE_LOOKUP:
        plan = _case_lookup_plan(user_query, hints)
    elif menu_kind is WorkflowKind.PROACTIVE_MONITORING:
        plan = _monitoring_plan(user_query, hints)
    elif menu_kind is WorkflowKind.UPLOAD_ANALYSIS:
        plan = _upload_plan(user_query, hints, force_commit=True)
    elif menu_kind is WorkflowKind.FINANCIAL_CALENDAR_IMPORT:
        plan = _financial_calendar_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.CASE_LOOKUP:
        plan = _case_lookup_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.UPLOAD_ANALYSIS:
        plan = _upload_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.FINANCIAL_CALENDAR_IMPORT:
        plan = _financial_calendar_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.PROACTIVE_MONITORING:
        plan = _monitoring_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.USER_REPORTED_DELAY:
        plan = _delay_plan(user_query, hints)
    elif hinted_kind is WorkflowKind.PRODUCT_ADVISORY_REPORT:
        plan = _product_plan(user_query, hints)
    elif _contains(user_query, "금융일정", "financial calendar", "금융 스케줄") and _contains(
        user_query,
        "업로드",
        "import",
        "가져오",
        "반영",
        "검증",
    ):
        plan = _financial_calendar_plan(user_query, hints)
    elif _contains(
        user_query,
        "무역파일 등록",
        "무역 파일 등록",
        "새로운 무역파일",
        "업로드",
        "문서 분석",
        "batch",
        "배치",
    ):
        plan = _upload_plan(user_query, hints)
    elif _contains(user_query, "모니터링", "monitor", "알림", "proactive"):
        plan = _monitoring_plan(user_query, hints)
    elif _contains(
        user_query,
        "지연",
        "delay",
        "늦어",
        "연기",
        "미뤄",
        "밀려",
    ) and _contains(user_query, "b/l", "bl", "선적", "발행"):
        plan = _delay_plan(user_query, hints)
    elif _contains(
        user_query,
        "상품",
        "대출",
        "팩토링",
        "usance",
        "유산스",
        "수출금융",
        "수입금융",
        "외화",
        "상담",
        "보고서",
        "브리핑",
        "rm",
    ):
        plan = _product_plan(user_query, hints)
    elif _contains(
        user_query,
        "tradecase",
        "trade case",
        "shipment 조회",
        "shipment 검색",
        "거래 조회",
        "거래 검색",
        "선적 조회",
        "선적 상태",
    ):
        plan = _case_lookup_plan(user_query, hints)
    else:
        return PlanningResponse(
            final_output=ConversationalResponse(
                response=(
                    "요청 범위를 확인하기 어렵습니다. 업로드 batch_id, 모니터링 기준일, "
                    "지연 case_id, 또는 찾을 KB 금융상품 목적을 알려주세요."
                )
            )
        )
    return PlanningResponse(final_output=plan)


def make_offline_planning_decision(
    *,
    user_query: str,
    hints: dict[str, Any],
) -> PlanningResponse:
    """Backward-compatible alias for offline tests and local deterministic runs."""
    return make_workflow_contract_decision(user_query=user_query, hints=hints)
