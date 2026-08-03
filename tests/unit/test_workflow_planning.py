from __future__ import annotations

from app.graphs.common_control import (
    _document_followups,
    _financial_link_detail_followups,
    _payment_gate_followups,
    _prefer_typed_workflow_contract,
    _product_filter_followups,
    _validate_human_value,
)
from app.schemas.planning import (
    ConversationalResponse,
    HumanIssue,
    Plan,
    PlanningResponse,
)
from app.services.workflow_planning import (
    START_MENU_TEXT,
    make_offline_planning_decision,
    make_workflow_contract_decision,
)


def _steps(query: str) -> list[dict[str, object]]:
    decision = make_offline_planning_decision(
        user_query=query,
        hints={"current_date": "2026-07-29", "company_id": "DEMO-A"},
    )
    return decision.final_output.model_dump(mode="json")["steps"]  # type: ignore[union-attr]


def _payload(
    query: str,
    hints: dict[str, object] | None = None,
) -> dict[str, object]:
    decision = make_offline_planning_decision(
        user_query=query,
        hints={
            "current_date": "2026-07-29",
            "company_id": "DEMO-A",
            **(hints or {}),
        },
    )
    return decision.final_output.model_dump(mode="json")


def test_greeting_and_menu_requests_return_the_four_choice_start_menu() -> None:
    for query in ("안녕하세요", "메뉴", "도움말"):
        assert _payload(query) == {"response": START_MENU_TEXT}

    assert START_MENU_TEXT.splitlines()[-4:] == [
        "1. 무역 TradeCase / Shipment 검색하기",
        "2. 금일 금융위험 거래 분석하기",
        "3. 새로운 무역파일 등록하기",
        "4. 새로운 금융일정 등록하기",
    ]


def test_menu_one_requests_case_id_then_reads_shipment_and_finance_snapshots() -> None:
    payload = _payload("1")
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "CASE_LOOKUP"
    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "interrupt/resume",
        "read_shipment_snapshot",
        "get_financial_risk_snapshot",
        "review_workflow_evidence",
    ]
    assert steps[0]["human_issue"]["issue_code"] == "CASE_LOOKUP_ID_REQUIRED"  # type: ignore[index]


def test_menu_one_uses_the_case_hint_without_an_extra_human_step() -> None:
    payload = _payload("1", {"case_ids": ["TRD-003"]})
    steps = payload["steps"]  # type: ignore[assignment]

    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "read_shipment_snapshot",
        "get_financial_risk_snapshot",
        "review_workflow_evidence",
    ]
    assert steps[0]["args"] == {"case_id": "TRD-003"}  # type: ignore[index]
    assert steps[1]["args"] == {  # type: ignore[index]
        "case_id": "TRD-003",
        "as_of_date": None,
    }


def test_menu_two_runs_and_persists_manual_monitoring() -> None:
    payload = _payload("2")
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "PROACTIVE_MONITORING"
    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "select_financial_monitoring_candidates",
        "review_workflow_evidence",
        "finalize_manual_monitoring",
    ]
    assert steps[-1]["args"] == {  # type: ignore[index]
        "as_of_date": "2026-07-29",
        "request_id": "${request_id}-manual-monitoring",
        "evidence_snapshot": "$task_results",
    }


def test_live_model_direct_response_cannot_bypass_menu_two_contract() -> None:
    model_decision = PlanningResponse(
        final_output=ConversationalResponse(response="오늘 위험은 없습니다.")
    )
    contract_decision = make_workflow_contract_decision(
        user_query="2",
        hints={"current_date": "2026-07-29", "company_id": "DEMO-A"},
    )

    selected, normalized = _prefer_typed_workflow_contract(
        model_decision=model_decision,
        contract_decision=contract_decision,
    )

    assert normalized is True
    assert isinstance(selected.final_output, Plan)
    assert selected.final_output.mission_type == "PROACTIVE_MONITORING"
    assert selected.final_output.steps[0].tool == "select_financial_monitoring_candidates"


def test_unrecognized_contract_does_not_suppress_a_valid_live_model_plan() -> None:
    model_plan = Plan(
        mission_type="DYNAMIC_SUPPORTED_PLAN",
        steps=[
            {
                "task_id": "read_shipment",
                "description": "Read shipment facts",
                "agent": "shipment_timeline",
                "tool": "read_shipment_snapshot",
                "args": {"case_id": "TRD-001"},
                "reason": "Shipment facts are required.",
            },
            {
                "task_id": "review",
                "description": "Review evidence",
                "agent": "critic",
                "tool": "review_workflow_evidence",
                "args": {},
                "reason": "Evidence must be reviewed.",
            },
        ],
    )
    model_decision = PlanningResponse(final_output=model_plan)
    contract_decision = PlanningResponse(
        final_output=ConversationalResponse(response="요청 범위를 확인하기 어렵습니다.")
    )

    selected, normalized = _prefer_typed_workflow_contract(
        model_decision=model_decision,
        contract_decision=contract_decision,
    )

    assert normalized is False
    assert selected is model_decision


def test_natural_late_bl_wording_routes_to_user_reported_delay() -> None:
    payload = _payload(
        "TRD-DEMO-003 거래의 B/L이 9일 늦어진다는 소식을 들었어. "
        "금융일정 충돌과 위험순위를 분석해줘."
    )
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "USER_REPORTED_DELAY"
    assert payload["case_ids"] == ["TRD-DEMO-003"]
    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "record_shipment_delay_scenarios",
        "inspect_payment_gate",
        "calculate_financial_exposure",
        "review_workflow_evidence",
    ]


def test_transaction_id_is_not_silently_used_as_trade_case_id() -> None:
    payload = _payload("TXN-DEMO-003 거래의 B/L이 9일 늦어진다는 소식을 들었어")
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "USER_REPORTED_DELAY"
    assert payload["case_ids"] == []
    assert steps[0]["human_issue"]["issue_code"] == "DELAY_CASE_ID_REQUIRED"  # type: ignore[index]
    record = next(step for step in steps if step["tool"] == "record_shipment_delay_scenarios")
    assert record["args"]["case_id"] == "$human.case_id"  # type: ignore[index]


def test_menu_three_requires_a_batch_and_includes_commit() -> None:
    payload = _payload("3")
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "UPLOAD_ANALYSIS"
    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "interrupt/resume",
        "stage_document_intelligence",
        "bundle_trade_cases",
        "commit_trade_cases",
        "review_workflow_evidence",
    ]
    assert steps[0]["human_issue"]["issue_code"] == "UPLOAD_BATCH_ID_REQUIRED"  # type: ignore[index]


def test_menu_four_requires_a_two_sheet_workbook_batch() -> None:
    payload = _payload("4")
    steps = payload["steps"]  # type: ignore[assignment]

    assert payload["mission_type"] == "FINANCIAL_CALENDAR_IMPORT"
    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "interrupt/resume",
        "validate_financial_calendar",
        "import_financial_calendar",
        "review_workflow_evidence",
    ]
    assert steps[0]["human_issue"]["issue_code"] == (  # type: ignore[index]
        "FINANCIAL_CALENDAR_SOURCE_REQUIRED"
    )


def test_financial_calendar_risk_request_selects_monitoring_candidates_after_import() -> None:
    payload = _payload("batch_id=FIN-4 금융일정 XLSX를 검증하고 반영한 뒤 충돌 위험을 분석해줘")
    steps = payload["steps"]  # type: ignore[assignment]

    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "validate_financial_calendar",
        "import_financial_calendar",
        "select_financial_monitoring_candidates",
        "review_workflow_evidence",
    ]
    assert steps[2]["args"] == {  # type: ignore[index]
        "company_id": "DEMO-A",
        "as_of_date": "2026-07-29",
        "limit": 100,
    }


def test_financial_calendar_storage_request_does_not_run_risk_analysis() -> None:
    payload = _payload(
        "batch_id=FIN-4의 2-Sheet 금융일정을 검증하고 금융 캘린더 DB에 저장해줘."
    )
    steps = payload["steps"]  # type: ignore[assignment]

    assert [step["tool"] for step in steps] == [  # type: ignore[index]
        "validate_financial_calendar",
        "import_financial_calendar",
        "review_workflow_evidence",
    ]


def test_missing_delay_days_produces_a_typed_human_issue_without_defaults() -> None:
    steps = _steps("TRD-003의 B/L 지연 영향 확인")

    issue_steps = [
        item
        for item in steps
        if item["agent"] == "human" and item["human_issue"]["issue_code"] == "DELAY_DAYS_REQUIRED"  # type: ignore[index]
    ]
    assert len(issue_steps) == 1
    assert issue_steps[0]["human_issue"]["value_type"] == "integer_list"  # type: ignore[index]
    assert not any(
        item["human_issue"] and item["human_issue"]["issue_code"] == "DELAY_CASE_ID_REQUIRED"  # type: ignore[index]
        for item in steps
    )
    record = next(item for item in steps if item["task_id"] == "record_delay")
    assert record["args"]["case_id"] == "TRD-003"  # type: ignore[index]
    assert record["args"]["expected_delay_days"] == "$human.delay_days"  # type: ignore[index]


def test_explicit_nine_day_delay_uses_report_time_and_etd_relative_input() -> None:
    steps = _steps("TRD-003 B/L 9일 지연 영향 확인")

    record = next(item for item in steps if item["task_id"] == "record_delay")
    assert record["args"] == {  # type: ignore[index]
        "case_id": "TRD-003",
        "reported_at": "2026-07-29",
        "request_id": "${request_id}-shipment-delay",
        "expected_delay_days": [9],
    }
    assert not any(item["task_id"] == "confirm_payment_anchor" for item in steps)
    calculate = next(item for item in steps if item["task_id"] == "calculate_financial_exposure")
    assert calculate["args"]["confirmed_anchor"] is None  # type: ignore[index]


def test_explicit_workflow_kind_overrides_incidental_delay_words() -> None:
    decision = make_workflow_contract_decision(
        user_query="TRD-003 B/L 지연 상황의 KB 상품과 고객/RM 브리핑을 만들어줘",
        hints={
            "workflow_kind": "PRODUCT_ADVISORY_REPORT",
            "case_ids": ["TRD-003"],
            "generate_report": True,
        },
    )
    payload = decision.final_output.model_dump(mode="json")  # type: ignore[union-attr]

    assert payload["mission_type"] == "PRODUCT_ADVISORY_REPORT"
    assert [step["tool"] for step in payload["steps"][:3]] == [
        "get_financial_risk_snapshot",
        "match_product_scenario",
        "retrieve_product_evidence",
    ]
    tools = [step["tool"] for step in payload["steps"]]
    assert "render_customer_report" in tools
    assert "render_rm_report" in tools
    assert "create_report_handoff" not in tools
    assert "compose_grounded_options" not in tools
    match = next(step for step in payload["steps"] if step["task_id"] == "match_product_scenario")
    assert match["args"]["financial_context"] == "$result.product_risk_snapshot"
    consent = next(step for step in payload["steps"] if step["task_id"] == "confirm_report_consent")
    assert consent["human_issue"]["issue_code"] == "REPORT_GENERATION_CONSENT"
    assert "인계" not in consent["human_issue"]["prompt"]
    assert not any(step["tool"] == "record_shipment_delay_scenarios" for step in payload["steps"])


def test_product_filter_asks_missing_borrower_once_and_rewrites_retrieval_refs() -> None:
    remaining = [
        {
            "task_id": "retrieve_product_evidence",
            "agent": "product_advisor",
            "tool": "retrieve_product_evidence",
            "args": {},
            "reason": "retrieve",
        }
    ]
    expanded = _product_filter_followups(
        state={"thread_id": "thread-product", "past_steps": []},
        task={
            "task_id": "match_product_scenario",
            "tool": "match_product_scenario",
            "args": {"query": "상품 추천", "requirements_reviewed": False},
        },
        result={
            "customer_role": "EXPORTER",
            "borrower_type": "UNKNOWN",
            "missing_filter_inputs": ["borrower_type"],
            "requirement_questions": [],
            "filter_context": {"company_id": "COMP-001", "case_id": "CASE-001"},
        },
        remaining=remaining,
    )

    assert [item["tool"] for item in expanded[:2]] == [
        "interrupt/resume",
        "match_product_scenario",
    ]
    assert expanded[0]["human_issue"]["response_key"] == "borrower_type"
    assert expanded[0]["human_issue"]["allowed_values"] == [
        "CORPORATION",
        "SOLE_PROPRIETOR",
    ]
    rematch_id = expanded[1]["task_id"]
    assert expanded[2]["args"]["matched_products"] == f"$result.{rematch_id}.products"


def test_product_filter_asks_only_candidate_specific_requirements() -> None:
    expanded = _product_filter_followups(
        state={"thread_id": "thread-product", "past_steps": []},
        task={
            "task_id": "rematch_product_after_borrower_comp_001",
            "tool": "match_product_scenario",
            "args": {"query": "상품 추천", "requirements_reviewed": False},
        },
        result={
            "customer_role": "EXPORTER",
            "borrower_type": "CORPORATION",
            "missing_filter_inputs": [],
            "requirement_questions": [
                {
                    "requirement_code": "KB_CREDIT_GRADE_REQUIREMENT",
                    "requirement_label": "KB 신용등급 요건 확인",
                    "requirement_summary": "KB 내부 신용등급 기준 충족 여부입니다.",
                    "product_ids": ["ONE-KB-CORPORATE-LOAN"],
                }
            ],
            "filter_context": {"company_id": "COMP-001", "case_id": "CASE-001"},
        },
        remaining=[
            {
                "task_id": "retrieve_product_evidence",
                "agent": "product_advisor",
                "tool": "retrieve_product_evidence",
                "args": {},
                "reason": "retrieve",
            }
        ],
    )

    assert [item["tool"] for item in expanded[:2]] == [
        "interrupt/resume",
        "match_product_scenario",
    ]
    issue = expanded[0]["human_issue"]
    assert issue["context"]["requirement_code"] == "KB_CREDIT_GRADE_REQUIREMENT"
    assert issue["context"]["requirement_summary"] == (
        "KB 내부 신용등급 기준 충족 여부입니다."
    )
    assert issue["value_type"] == "boolean"
    assert expanded[1]["args"]["requirements_reviewed"] is True


def test_product_requirement_human_issues_use_short_korean_explanations() -> None:
    expanded = _product_filter_followups(
        state={"thread_id": "thread-product-help", "past_steps": []},
        task={
            "task_id": "match_product_scenario",
            "tool": "match_product_scenario",
            "args": {"query": "상품 추천", "requirements_reviewed": False},
        },
        result={
            "customer_role": "EXPORTER",
            "borrower_type": "CORPORATION",
            "missing_filter_inputs": [],
            "requirement_questions": [
                {
                    "requirement_code": "STRATEGIC_TARGET_COMPANY",
                    "requirement_label": "전략 타깃 기업 요건 확인",
                    "requirement_summary": (
                        "우수 기술력 등을 보유한 전략 타깃 중소기업에 해당하는지 확인합니다."
                    ),
                    "product_ids": ["KB-MOADREAM-LOAN"],
                },
                {
                    "requirement_code": "KSURE_PROGRAM_ELIGIBILITY",
                    "requirement_label": "K-SURE 지원 프로그램 요건 확인",
                    "requirement_summary": (
                        "K-SURE 보증서·보험증권을 활용할 수 있는 지원 대상인지 확인합니다."
                    ),
                    "product_ids": ["KB-KSURE-TRADE-SUPPORT"],
                },
            ],
            "filter_context": {"company_id": "COMP-001", "case_id": "CASE-001"},
        },
        remaining=[],
    )

    human_issues = [item["human_issue"] for item in expanded if item["agent"] == "human"]
    assert [issue["prompt"] for issue in human_issues] == [
        "전략 타깃 기업에 해당합니까?",
        "K-SURE 지원 프로그램 대상에 해당합니까?",
    ]
    assert all(issue["context"]["requirement_summary"] for issue in human_issues)


def test_payment_anchor_issue_is_injected_only_for_repairable_confirmation_gate() -> None:
    remaining = [
        {
            "task_id": "calculate_financial_exposure",
            "agent": "financial_exposure",
            "tool": "calculate_financial_exposure",
            "args": {"case_id": "TRD-003", "confirmed_anchor": None},
            "reason": "finance",
        }
    ]
    permitted = _payment_gate_followups(
        state={"case_ids": ["TRD-003"]},
        result={"case_id": "TRD-003", "permitted": True},
        remaining=remaining,
    )
    assert permitted == remaining

    gated = _payment_gate_followups(
        state={"case_ids": ["TRD-003"]},
        result={
            "case_id": "TRD-003",
            "obligation_id": "OBL-3",
            "permitted": False,
            "calculation_allowed": "AFTER_CONFIRMATION",
            "raw_text": "T/T 45 DAYS AFTER B/L DATE",
            "tenor_days": 45,
            "day_type_effective": "CALENDAR",
            "customer_question": "기준일을 확인해 주세요.",
            "missing_fields": ["anchor_type_effective"],
        },
        remaining=remaining,
    )
    assert gated[0]["agent"] == "human"
    assert gated[0]["human_issue"]["allowed_values"] == [  # type: ignore[index]
        "ON_BOARD_DATE",
        "CANCEL",
    ]
    assert gated[1]["args"]["confirmed_anchor"] == "$human.confirmed_anchor"  # type: ignore[index]

    denied = _payment_gate_followups(
        state={"case_ids": ["TRD-003"]},
        result={
            "case_id": "TRD-003",
            "permitted": False,
            "calculation_allowed": "NO",
            "missing_fields": ["anchor_type_effective", "tenor_days"],
        },
        remaining=remaining,
    )
    assert denied == remaining


def test_financial_link_data_action_expands_to_human_update_and_recalculation() -> None:
    task = {
        "task_id": "calculate_financial_exposure",
        "agent": "financial_exposure",
        "tool": "calculate_financial_exposure",
        "args": {
            "case_id": "TRD-003",
            "request_id": "${request_id}-financial-exposure",
            "confirmed_anchor": "$human.confirmed_anchor",
        },
        "reason": "finance",
    }
    remaining = [
        {
            "task_id": "evidence_review",
            "agent": "critic",
            "tool": "review_workflow_evidence",
            "args": {"expected_task_ids": ["calculate_financial_exposure"]},
            "reason": "review",
        }
    ]
    result = {
        "case_id": "TRD-003",
        "data_actions": [
            {
                "code": "FINANCIAL_LINK_DETAILS_REQUIRED",
                "case_id": "TRD-003",
                "company_id": "A",
                "transaction_id": "TXN003",
                "transaction_event_link_id": "LNK-003-FE001",
                "financial_event_id": "FE001",
                "missing_fields": ["dependency_scope"],
                "message": "scope required",
            }
        ],
    }

    followups = _financial_link_detail_followups(
        state={"past_steps": [], "case_ids": ["TRD-003"]},
        task=task,
        result=result,
        remaining=remaining,
    )

    assert [item["tool"] for item in followups] == [
        "interrupt/resume",
        "update_financial_event_link_details",
        "calculate_financial_exposure",
        "review_workflow_evidence",
    ]
    issue, update, recalc, critic = followups
    assert issue["human_issue"]["allowed_values"] == ["FULL", "PARTIAL", "CANCEL"]  # type: ignore[index]
    assert update["args"] == {
        "company_id": "A",
        "case_id": "TRD-003",
        "transaction_id": "TXN003",
        "transaction_event_link_id": "LNK-003-FE001",
        "details": {"dependency_scope": "$human.financial_link_scope_lnk_003_fe001_scope"},
        "actor": "human:${thread_id}",
    }
    assert recalc["args"]["confirmed_anchor"] == "$human.confirmed_anchor"  # type: ignore[index]
    assert recalc["task_id"] in critic["args"]["expected_task_ids"]  # type: ignore[index]
    assert update["task_id"] in critic["args"]["expected_task_ids"]  # type: ignore[index]


def test_partial_financial_link_amount_currency_has_typed_human_normalization() -> None:
    issue = HumanIssue(
        issue_code="FINANCIAL_LINK_AMOUNT_REQUIRED:LNK-1",
        prompt="금액과 통화",
        response_key="link_amount",
        value_type="amount_currency",
    )
    assert _validate_human_value(issue, "30,000 usd") == {
        "linked_amount": 30_000.0,
        "linked_currency": "USD",
    }
    assert _validate_human_value(issue, {"amount": 42_000, "currency": "eur"}) == {
        "linked_amount": 42_000.0,
        "linked_currency": "EUR",
    }


def test_only_exact_field_requests_expand_to_human_steps() -> None:
    state = {"past_steps": [], "thread_id": "thread-1"}
    missing_document_only = {
        "batch_id": "BATCH-9",
        "human_requests": [],
        "missing_document_issues": [{"case_id": "TRD-003", "missing_document": "BILL_OF_LADING"}],
    }
    assert (
        _document_followups(
            state=state,
            result=missing_document_only,
            remaining=[],
        )
        == []
    )

    exact_field = {
        "batch_id": "BATCH-9",
        "human_requests": [
            {
                "request_id": "REQ-BL-ONBOARD",
                "request_type": "DOCUMENT_FIELD_VALUE",
                "case_id": "TRD-003",
                "document_id": "DOC-BL-3",
                "doc_type": "BILL_OF_LADING",
                "exact_standard_field": "on_board_date",
                "field_path": "BILL_OF_LADING.on_board_date",
                "prompt": "B/L on-board date를 입력해 주세요.",
                "status": "OPEN",
            }
        ],
        "missing_document_issues": [],
    }
    followups = _document_followups(
        state=state,
        result=exact_field,
        remaining=[],
    )
    assert [item["agent"] for item in followups] == [
        "human",
        "trade_case_manager",
    ]
    assert followups[0]["human_issue"]["issue_code"] == "REQ-BL-ONBOARD"  # type: ignore[index]
    assert followups[1]["args"]["exact_standard_field"] == "on_board_date"  # type: ignore[index]
