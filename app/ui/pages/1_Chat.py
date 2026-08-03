from __future__ import annotations

import base64
import html
import json
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from app.services.product_advisory import (
    PRODUCT_REQUIREMENT_SUMMARIES,
    product_payload_for_user,
)
from app.services.workflow_planning import START_MENU_TEXT
from app.ui import api_client
from app.ui.theme import api_guard, setup_page

MENU_OPTIONS = {
    "1": ("TradeCase / Shipment 검색", "CASE_LOOKUP", "🔎"),
    "2": ("금일 금융위험 거래 분석", "PROACTIVE_MONITORING", "⚡"),
    "3": ("새로운 무역파일 등록", "UPLOAD_ANALYSIS", "📄"),
    "4": ("새로운 금융일정 등록", "FINANCIAL_CALENDAR_IMPORT", "📅"),
}

DEMO_SCENARIOS = {
    "직접 사용": ("", "", "", ""),
    "Demo 0 · 8개 혼합문서·3거래 자동 매칭": (
        "3",
        "UPLOAD_ANALYSIS",
        "",
        "DEMO0-CO",
    ),
    "Demo 1 · 금일 B/L 미수령 금융위험 분석": (
        "2",
        "PROACTIVE_MONITORING",
        "2",
        "DEMO1-CO",
    ),
    "Demo 2 · 사전 통보된 9일 선적 지연 분석": (
        "",
        "USER_REPORTED_DELAY",
        (
            "CASE-DEMO2 거래의 B/L이 계획보다 9일 늦어진다는 소식을 들었어. "
            "금융일정 충돌과 위험순위를 분석해줘."
        ),
        "DEMO2-CO",
    ),
}

WELCOME_MESSAGE = (
    "안녕하세요! **KB TradeFlow Twin**입니다.  "
    "\n원하시는 업무를 아래에서 선택하거나 직접 말씀해 주세요."
)

KB_LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "KB_SymbolMark.png"
KB_LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(KB_LOGO_PATH.read_bytes()).decode(
    "ascii"
)


def _append_chat(role: str, content: object) -> None:
    text = str(content or "").strip()
    if text:
        st.session_state.tradeflow_chat_history.append({"role": role, "content": text})


def _next_upload_batch_id(batch_kind: str) -> str:
    """Create a hidden, collision-safe sequential ID for one upload kind."""
    normalized_kind = batch_kind.strip().upper()
    if normalized_kind not in {"TRADE", "FIN"}:
        raise ValueError(f"Unsupported upload batch kind: {batch_kind}")

    namespace = str(st.session_state.get("tradeflow_upload_namespace") or "").strip()
    if not namespace:
        namespace = uuid.uuid4().hex[:8].upper()
        st.session_state.tradeflow_upload_namespace = namespace

    sequence_key = f"tradeflow_{normalized_kind.lower()}_upload_sequence"
    try:
        sequence = max(1, int(st.session_state.get(sequence_key, 1)))
    except (TypeError, ValueError):
        sequence = 1
    st.session_state[sequence_key] = sequence + 1
    return f"{normalized_kind}-{namespace}-{sequence:04d}"


def _select_menu(menu_id: str) -> None:
    st.session_state.pop("tradeflow_active_batch_id", None)
    st.session_state.pop("tradeflow_last_result", None)
    st.session_state.pop("tradeflow_consultation_demo", None)
    st.session_state.tradeflow_selected_menu = menu_id
    st.session_state.tradeflow_workflow_kind = MENU_OPTIONS[menu_id][1]
    st.session_state.tradeflow_queued_question = ""
    st.session_state.tradeflow_demo_scenario = "직접 사용"
    if menu_id == "2":
        st.session_state.tradeflow_queued_question = "금일 금융위험 거래 분석해."
        st.session_state.tradeflow_auto_submit = True


def _select_demo() -> None:
    # Every judged scenario owns an independent Agent Server Thread.  Reusing
    # the previous Thread would also reuse its Human answers and checkpointed
    # business context, which can mix two demo companies in one execution.
    _retry_graph_connection()
    for key in (
        "tradeflow_pending_confirmation",
        "tradeflow_last_result",
        "tradeflow_consultation_demo",
    ):
        st.session_state.pop(key, None)
    st.session_state.tradeflow_chat_history = [
        {"role": "assistant", "content": WELCOME_MESSAGE}
    ]
    st.session_state.pop("tradeflow_active_batch_id", None)
    menu_id, workflow_kind, question, company_id = DEMO_SCENARIOS[
        st.session_state.tradeflow_demo_scenario
    ]
    st.session_state.tradeflow_selected_menu = menu_id
    st.session_state.tradeflow_workflow_kind = workflow_kind
    st.session_state.tradeflow_queued_question = question
    if company_id:
        st.session_state.tradeflow_company_id = company_id
    st.session_state.tradeflow_auto_submit = bool(question and menu_id not in {"3", "4"})


def _queue_question(
    question: str,
    workflow_kind: str,
    menu_id: str = "",
) -> None:
    st.session_state.pop("tradeflow_last_result", None)
    st.session_state.pop("tradeflow_consultation_demo", None)
    st.session_state.tradeflow_queued_question = question
    st.session_state.tradeflow_workflow_kind = workflow_kind
    st.session_state.tradeflow_selected_menu = menu_id
    st.session_state.tradeflow_auto_submit = True


def _reset_chat() -> None:
    company_id = str(st.session_state.get("tradeflow_company_id") or "DEMO-A").strip()
    widget_keys = {
        "chat-trade-files",
        "chat-financial-calendar",
        "chat-composer",
        "tradeflow_lookup_case_id",
    }
    for key in list(st.session_state):
        if key.startswith("tradeflow_") or key in widget_keys:
            del st.session_state[key]
    st.session_state.tradeflow_company_id = company_id or "DEMO-A"


def _ensure_chat_thread() -> None:
    """Create one real Agent Server thread and retain it for this UI session."""
    if st.session_state.get("tradeflow_graph_connected") is True and st.session_state.get(
        "tradeflow_chat_thread_id"
    ):
        return
    if st.session_state.get("tradeflow_thread_init_attempted"):
        return
    st.session_state.tradeflow_thread_init_attempted = True
    st.session_state.pop("tradeflow_chat_thread_id", None)
    result = api_guard(
        lambda: api_client.post(
            "/api/graph/threads",
            {"source": "streamlit-chat"},
        )
    )
    if not isinstance(result, dict):
        st.session_state.tradeflow_graph_connected = False
        return
    thread_id = str(result.get("thread_id") or "").strip()
    connected = result.get("connected") is True and bool(thread_id)
    st.session_state.tradeflow_graph_connected = connected
    st.session_state.tradeflow_graph_id = str(result.get("graph_id") or "chat")
    if connected:
        st.session_state.tradeflow_chat_thread_id = thread_id


def _retry_graph_connection() -> None:
    """Forget only connection state so the next render creates a fresh Thread."""
    for key in (
        "tradeflow_chat_thread_id",
        "tradeflow_graph_connected",
        "tradeflow_graph_id",
        "tradeflow_thread_init_attempted",
    ):
        st.session_state.pop(key, None)


def _set_consultation_demo_choice(report_key: str, choice: str) -> None:
    """Keep the UI-only consultation choice across Streamlit reruns."""
    st.session_state.tradeflow_consultation_demo = {
        "report_key": report_key,
        "choice": choice,
    }


def _show_consultation_demo(report_key: str) -> None:
    """Render the consultation CTA without triggering a handoff or API call."""
    selection = st.session_state.get("tradeflow_consultation_demo")
    choice = ""
    if isinstance(selection, dict) and selection.get("report_key") == report_key:
        choice = str(selection.get("choice") or "")

    if choice == "connected":
        st.success(
            "상담사 연결 요청이 접수되었습니다.\n\n※ 데모에서는 연결 요청 화면까지만 제공합니다."
        )
        return
    if choice == "later":
        return

    st.markdown("KB 무역금융 상담사에게 연결해 드릴까요?")
    connect_col, later_col = st.columns(2)
    with connect_col:
        st.button(
            "상담사 연결",
            key=f"consultation-connect-{report_key}",
            type="primary",
            use_container_width=True,
            on_click=_set_consultation_demo_choice,
            args=(report_key, "connected"),
        )
    with later_col:
        st.button(
            "나중에",
            key=f"consultation-later-{report_key}",
            use_container_width=True,
            on_click=_set_consultation_demo_choice,
            args=(report_key, "later"),
        )


def _show_report_links(result: dict[str, Any]) -> bool:
    """Render both report downloads directly, without creating a handoff record."""
    results = result.get("results")
    if not isinstance(results, dict):
        return False
    customer = results.get("render_customer_report")
    rm = results.get("render_rm_report")
    if not isinstance(customer, dict) or not isinstance(rm, dict):
        return False
    if not customer.get("asset_path") or not rm.get("asset_path"):
        return False

    case_id = str(customer.get("case_id") or rm.get("case_id") or "").strip()
    if not case_id:
        return False
    report_key = f"{result.get('request_id') or 'current'!s}:{case_id}"
    st.success("보고서가 생성되었습니다.")
    customer_col, rm_col = st.columns(2)
    with customer_col:
        st.link_button(
            "고객용 PDF 받기",
            f"{api_client.API_BASE}/api/reports/customer/{case_id}",
            use_container_width=True,
        )
    with rm_col:
        st.link_button(
            "RM용 PDF 받기",
            f"{api_client.API_BASE}/api/reports/rm/{case_id}",
            use_container_width=True,
        )
    _show_consultation_demo(report_key)
    return True


def _show_next_actions(result: dict[str, Any], *, reports_ready: bool) -> None:
    actions = result.get("next_actions")
    if not isinstance(actions, list):
        return
    request_id = str(result.get("request_id") or "current")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("id") or f"action-{index}")
        message = str(action.get("message") or "").strip()
        if action_id == "register_financial_calendar":
            st.info("금융결제 조건이 있으세요?")
            st.button(
                "📅 2-Sheet 금융일정 등록하기",
                key=f"next-financial-calendar-{request_id}",
                on_click=_select_menu,
                args=("4",),
                use_container_width=True,
            )
        elif action_id == "build_conflict_report":
            st.info("충돌 종합 보고서를 만들어 드릴까요?")
            st.button(
                "📊 고객용·RM용 종합 보고서 생성",
                key=f"next-conflict-report-{request_id}",
                on_click=_queue_question,
                args=(message, "PRODUCT_ADVISORY_REPORT", ""),
                use_container_width=True,
            )
        elif action_id == "suggest_rm_contact" and not reports_ready:
            st.info("KB 무역상담사에게 연락해 드릴까요?")


def _show_result_actions(result: dict[str, Any]) -> None:
    source_scope = html.escape(str(result.get("source_scope") or "CASE_DB"))
    source_status = html.escape(str(result.get("source_status") or "GRAPH"))
    st.markdown(
        f'<span class="kb-chip">{source_scope}</span><span class="kb-chip">{source_status}</span>',
        unsafe_allow_html=True,
    )
    reports_ready = _show_report_links(result)
    _show_next_actions(result, reports_ready=reports_ready)
    with st.expander("멀티 에이전트 실행 근거"):
        st.json(product_payload_for_user(result))


def _coerce_human_answer(issue: dict[str, Any], raw: Any) -> Any:
    value_type = str(issue.get("value_type") or "string")
    if value_type == "integer":
        return int(raw)
    if value_type in {"integer_list", "string_list"}:
        parsed = json.loads(str(raw))
        if not isinstance(parsed, list):
            raise ValueError("배열 형식으로 입력해 주세요.")
        if value_type == "integer_list":
            return [int(item) for item in parsed]
        return [str(item) for item in parsed]
    return raw


def _resume_human(
    pending: dict[str, Any],
    answer: Any,
    *,
    display_answer: object | None = None,
) -> None:
    if not st.session_state.get("tradeflow_graph_connected"):
        st.warning("LangGraph Dev 연결 후 다시 시도해 주세요.")
        return
    _append_chat("user", answer if display_answer is None else display_answer)
    with st.spinner("입력값을 반영하고 다음 작업을 이어가는 중입니다…"):
        result = api_guard(
            lambda: api_client.post(
                f"/api/threads/{st.session_state.tradeflow_chat_thread_id}/resume",
                {"issue_code": pending["issue_code"], "value": answer},
            )
        )
    if not isinstance(result, dict):
        _append_chat("assistant", "입력값을 반영하지 못했습니다. 잠시 후 다시 시도해 주세요.")
        st.rerun()
    st.session_state.pop("tradeflow_pending_confirmation", None)
    if result.get("status") == "HUMAN_REQUIRED":
        next_issue = result.get("issue")
        if isinstance(next_issue, dict):
            st.session_state.tradeflow_pending_confirmation = next_issue
            _append_chat("assistant", next_issue.get("prompt"))
    else:
        _append_chat("assistant", result.get("answer") or result.get("status"))
        st.session_state.tradeflow_last_result = result
    st.rerun()


def _submit_chat(question: str, scope: str, case_id: str) -> None:
    if not question.strip():
        st.warning("질문을 입력해 주세요.")
        return
    if not st.session_state.get("tradeflow_graph_connected"):
        st.warning("LangGraph Dev 연결 후 질문을 실행해 주세요.")
        return
    _append_chat("user", question)
    with st.spinner("전문 Agent들이 계획을 실행하고 있습니다…"):
        result = api_guard(
            lambda: api_client.post(
                "/api/chat",
                {
                    "thread_id": st.session_state.tradeflow_chat_thread_id,
                    "message": question,
                    "company_id": st.session_state.tradeflow_company_id,
                    "case_id": case_id or None,
                    "source_scope": scope,
                    "workflow_kind": st.session_state.tradeflow_workflow_kind or None,
                    "batch_id": st.session_state.get("tradeflow_active_batch_id"),
                    "generate_report": (
                        st.session_state.tradeflow_workflow_kind == "PRODUCT_ADVISORY_REPORT"
                    ),
                },
            )
        )
    if not isinstance(result, dict):
        _append_chat("assistant", "요청을 처리하지 못했습니다. 연결 상태를 확인해 주세요.")
        st.rerun()
    if result.get("status") == "HUMAN_REQUIRED":
        issue = result.get("issue")
        if isinstance(issue, dict):
            st.session_state.tradeflow_pending_confirmation = issue
            _append_chat("assistant", issue.get("prompt"))
        st.session_state.pop("tradeflow_last_result", None)
        st.rerun()
    _append_chat("assistant", result.get("answer") or result.get("status"))
    st.session_state.tradeflow_last_result = result
    st.rerun()


def _store_financial_calendar(batch_id: str) -> dict[str, Any] | None:
    """Validate and persist an uploaded workbook without running risk analysis."""
    if not st.session_state.get("tradeflow_graph_connected"):
        st.warning("LangGraph Dev 연결 후 다시 시도해 주세요.")
        return None
    result = api_guard(
        lambda: api_client.post(
            "/api/chat",
            {
                "thread_id": st.session_state.tradeflow_chat_thread_id,
                "message": (
                    f"batch_id={batch_id}의 2-Sheet 금융일정을 검증하고 "
                    "금융 캘린더 DB에 저장해줘."
                ),
                "company_id": st.session_state.tradeflow_company_id,
                "source_scope": st.session_state.tradeflow_source_scope,
                "workflow_kind": "FINANCIAL_CALENDAR_IMPORT",
                "batch_id": batch_id,
                "generate_report": False,
            },
        )
    )
    if not isinstance(result, dict):
        return None
    if result.get("status") == "HUMAN_REQUIRED":
        issue = result.get("issue")
        if isinstance(issue, dict):
            st.session_state.tradeflow_pending_confirmation = issue
            _append_chat("assistant", issue.get("prompt"))
        return None
    if str(result.get("status") or "").upper() != "SUCCESS":
        st.error("금융일정을 저장하지 못했습니다. 입력값과 회사 범위를 확인해 주세요.")
        return None
    return result


def _render_menu() -> None:
    st.markdown('<div class="kb-section-label">Quick actions</div>', unsafe_allow_html=True)
    menu_items = list(MENU_OPTIONS.items())
    for offset in range(0, len(menu_items), 2):
        columns = st.columns(2)
        for column, (menu_id, (label, _, icon)) in zip(
            columns,
            menu_items[offset : offset + 2],
            strict=False,
        ):
            with column:
                st.button(
                    f"{icon}  {label}",
                    key=f"main-menu-{menu_id}",
                    on_click=_select_menu,
                    args=(menu_id,),
                    use_container_width=True,
                    type=(
                        "primary"
                        if st.session_state.tradeflow_selected_menu == menu_id
                        else "secondary"
                    ),
                )


def _render_case_lookup() -> None:
    with st.container(border=True):
        st.markdown("**조회할 TradeCase를 알려주세요**")
        st.caption("저장된 거래·선적 상태와 최신 금융위험 결과를 함께 조회합니다.")
        case_id = st.text_input(
            "TradeCase ID",
            key="tradeflow_lookup_case_id",
            placeholder="예: TRD-DEMO-001",
            label_visibility="collapsed",
        )
        st.button(
            "거래·선적 조회",
            key="execute-case-lookup",
            type="primary",
            use_container_width=True,
            disabled=not case_id.strip(),
            on_click=_queue_question,
            args=(
                f"{case_id.strip()} TradeCase와 Shipment 상태를 조회해줘.",
                "CASE_LOOKUP",
                "1",
            ),
        )


def _render_trade_upload() -> None:
    with st.container(border=True):
        st.markdown("**Booking · Invoice · B/L 일괄 등록**")
        st.caption("여러 PDF를 거래별로 묶고, 필수 문서·필드 누락을 확인합니다.")
        trade_files = st.file_uploader(
            "무역 PDF 여러 개",
            type=["pdf"],
            accept_multiple_files=True,
            key="chat-trade-files",
        )
        if st.button(
            "업로드 후 누락 분석·DB 반영",
            key="chat-upload-trade-files",
            type="primary",
            use_container_width=True,
            disabled=not trade_files,
        ):
            batch_id = _next_upload_batch_id("TRADE")
            with st.spinner("파일을 안전하게 업로드하는 중입니다…"):
                uploaded = api_guard(lambda: api_client.upload(batch_id, trade_files))
            if isinstance(uploaded, dict):
                st.session_state.tradeflow_active_batch_id = batch_id
                st.session_state.tradeflow_workflow_kind = "UPLOAD_ANALYSIS"
                st.session_state.tradeflow_selected_menu = ""
                st.session_state.tradeflow_queued_question = (
                    f"batch_id={batch_id}의 Booking, Invoice, B/L을 거래별로 묶고 "
                    "필수 문서·필드 누락을 확인한 뒤 DB에 반영해줘."
                )
                st.session_state.tradeflow_auto_submit = True
                _append_chat("user", f"무역파일 {uploaded.get('file_count', 0)}개 업로드")
                _append_chat("assistant", "업로드를 완료했습니다.")
                st.rerun()


def _render_calendar_upload() -> None:
    with st.container(border=True):
        st.markdown("**금융결제 조건 2-Sheet 등록**")
        st.caption(
            "1.금융이벤트와 2.거래연결 XLSX를 검증해 금융 캘린더에 저장합니다. "
            "충돌 분석은 업무 바로가기의 '금일 금융위험 거래 분석'에서 실행합니다."
        )
        calendar_file = st.file_uploader(
            "금융일정 XLSX 1개",
            type=["xlsx"],
            accept_multiple_files=False,
            key="chat-financial-calendar",
        )
        if st.button(
            "금융일정 검증·저장",
            key="chat-upload-financial-calendar",
            type="primary",
            use_container_width=True,
            disabled=calendar_file is None,
        ):
            batch_id = _next_upload_batch_id("FIN")
            files = [calendar_file] if calendar_file is not None else []
            with st.spinner("금융일정을 검증하고 캘린더에 저장하는 중입니다…"):
                uploaded = api_guard(lambda: api_client.upload(batch_id, files))
                stored = (
                    _store_financial_calendar(batch_id)
                    if isinstance(uploaded, dict)
                    else None
                )
            if isinstance(stored, dict):
                st.session_state.pop("tradeflow_active_batch_id", None)
                st.session_state.pop("tradeflow_last_result", None)
                st.session_state.tradeflow_workflow_kind = ""
                st.session_state.tradeflow_selected_menu = ""
                _append_chat(
                    "assistant",
                    stored.get("answer")
                    or "업로드를 완료했습니다. 금융일정을 캘린더 DB에 반영했습니다.",
                )
                st.rerun()


def _render_human_confirmation(pending: dict[str, Any]) -> None:
    issue_code = str(pending.get("issue_code") or "pending")
    confirmation_key = str(
        pending.get("confirmation_id") or st.session_state.get("tradeflow_confirmation_seq", 0)
    )
    with st.container(border=True):
        st.markdown("**확인이 필요한 항목이 있어요**")
        st.caption(str(pending.get("prompt") or "추가 확인값을 입력해 주세요."))
        context = pending.get("context") or {}
        requirement_code = (
            str(context.get("requirement_code") or "").strip().upper()
            if isinstance(context, dict)
            else ""
        )
        requirement_summary = (
            str(context.get("requirement_summary") or "").strip()
            if isinstance(context, dict)
            else ""
        )
        if not requirement_summary:
            requirement_summary = PRODUCT_REQUIREMENT_SUMMARIES.get(requirement_code, "")
        if requirement_summary:
            st.info(requirement_summary, icon="ℹ️")
        allowed_values = pending.get("allowed_values") or []
        if allowed_values:
            labels = {
                "YES": "예",
                "NO": "아니오",
                "UNKNOWN": "모름",
                "TRUE": "동의",
                "FALSE": "동의하지 않음",
                "FULL": "전액 의존",
                "PARTIAL": "일부 의존",
                "ON_BOARD_DATE": "본선적재일 적용",
                "CANCEL": "취소",
                "CORPORATION": "법인사업자",
                "SOLE_PROPRIETOR": "개인사업자",
            }
            if issue_code.startswith("PRODUCT_REQUIREMENT:"):
                labels["TRUE"] = "예"
                labels["FALSE"] = "아니오"
            primary_values = {"YES", "TRUE", "FULL", "ON_BOARD_DATE", "CORPORATION"}
            borrower_aliases = {
                "법인사업자": "CORPORATION",
                "개인사업자": "SOLE_PROPRIETOR",
            }
            unique_values: list[tuple[object, str]] = []
            seen_values: set[str] = set()
            for value in allowed_values:
                normalized_value = str(value).strip().upper()
                canonical_value = borrower_aliases.get(str(value).strip(), normalized_value)
                if canonical_value in seen_values:
                    continue
                seen_values.add(canonical_value)
                resume_value: object = (
                    canonical_value
                    if canonical_value in {"CORPORATION", "SOLE_PROPRIETOR"}
                    else value
                )
                unique_values.append((resume_value, canonical_value))
            columns = st.columns(len(unique_values))
            for column, (value, normalized_value) in zip(
                columns,
                unique_values,
                strict=False,
            ):
                with column:
                    if st.button(
                        labels.get(normalized_value, str(value)),
                        key=f"human-{issue_code}-{confirmation_key}-{value}",
                        use_container_width=True,
                        type="primary" if normalized_value in primary_values else "secondary",
                    ):
                        _resume_human(
                            pending,
                            value,
                            display_answer=labels.get(normalized_value, str(value)),
                        )
            return

        raw = st.text_input(
            "답변",
            key=f"human-answer-{issue_code}-{confirmation_key}",
            placeholder="요청된 값을 입력해 주세요.",
            label_visibility="collapsed",
        )
        if st.button(
            "응답하고 계속",
            key=f"resume-human-{issue_code}-{confirmation_key}",
            type="primary",
            use_container_width=True,
            disabled=not raw,
        ):
            try:
                answer = _coerce_human_answer(pending, raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                st.error("요청된 형식으로 값을 입력해 주세요.")
            else:
                _resume_human(pending, answer)


setup_page("Chat", "💬", chat_mode=True)

_ensure_chat_thread()
if "tradeflow_chat_history" not in st.session_state or st.session_state.tradeflow_chat_history == [
    {"role": "assistant", "content": START_MENU_TEXT}
]:
    st.session_state.tradeflow_chat_history = [{"role": "assistant", "content": WELCOME_MESSAGE}]
if "tradeflow_selected_menu" not in st.session_state:
    st.session_state.tradeflow_selected_menu = ""
if "tradeflow_workflow_kind" not in st.session_state:
    st.session_state.tradeflow_workflow_kind = ""
if "tradeflow_queued_question" not in st.session_state:
    st.session_state.tradeflow_queued_question = st.session_state.pop("tradeflow_question", "")
if "tradeflow_demo_scenario" not in st.session_state:
    st.session_state.tradeflow_demo_scenario = "직접 사용"
if "tradeflow_source_scope" not in st.session_state:
    st.session_state.tradeflow_source_scope = "CASE_DB"
if "tradeflow_case_hint" not in st.session_state:
    st.session_state.tradeflow_case_hint = ""
if "tradeflow_company_id" not in st.session_state:
    st.session_state.tradeflow_company_id = "DEMO-A"
if "tradeflow_confirmation_seq" not in st.session_state:
    st.session_state.tradeflow_confirmation_seq = 0

st.markdown(
    f"""
    <div class="kb-chat-header">
      <div class="kb-chat-brand">
        <div class="kb-chat-mark">
          <img src="{KB_LOGO_DATA_URI}" alt="KB Star-b symbol" />
        </div>
        <div>
          <div class="kb-chat-title">KB TradeFlow Twin</div>
          <div class="kb-chat-subtitle">Multi-Agent Trade Assistant</div>
        </div>
      </div>
      <div class="kb-online">8 Agents ready</div>
    </div>
    """,
    unsafe_allow_html=True,
)

graph_connected = st.session_state.get("tradeflow_graph_connected") is True

session_col, settings_col, reset_col = st.columns([5.2, 1.15, 1.2], vertical_alignment="center")
with session_col:
    if graph_connected:
        st.markdown(
            '<div class="kb-graph-status kb-graph-connected">'
            '<span class="kb-graph-dot"></span>LangGraph 연결됨</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="kb-graph-status kb-graph-disconnected">'
            '<span class="kb-graph-dot"></span>LangGraph 연결 안 됨</div>',
            unsafe_allow_html=True,
        )
with settings_col, st.popover("설정", icon=":material/tune:", use_container_width=True):
    st.text_input(
        "로그인 회사 ID",
        key="tradeflow_company_id",
        help="현재 상담 세션의 회사 범위입니다. 금융일정은 이 회사의 기존 TradeCase에만 연결됩니다.",
    )
    st.selectbox(
        "시연 시나리오",
        list(DEMO_SCENARIOS),
        key="tradeflow_demo_scenario",
        on_change=_select_demo,
    )
    st.selectbox(
        "Source scope",
        ["CASE_DB", "KB_ONLY"],
        key="tradeflow_source_scope",
    )
    st.text_input(
        "TradeCase ID 힌트",
        key="tradeflow_case_hint",
        placeholder="선택 입력",
    )
with reset_col:
    st.button(
        "새 대화",
        key="reset-tradeflow-chat",
        on_click=_reset_chat,
        use_container_width=True,
    )

if not graph_connected:
    st.caption("LangGraph Dev(:2024)를 먼저 실행한 뒤 연결을 다시 시도해 주세요.")
    st.button(
        "LangGraph 다시 연결",
        key="retry-langgraph-connection",
        on_click=_retry_graph_connection,
        use_container_width=True,
    )

for message in st.session_state.tradeflow_chat_history:
    role = str(message.get("role") or "assistant")
    avatar = ":material/person:" if role == "user" else ":material/smart_toy:"
    with st.chat_message(role, avatar=avatar):
        st.markdown(str(message.get("content") or ""))

is_fresh_chat = len(st.session_state.tradeflow_chat_history) <= 1
if is_fresh_chat:
    _render_menu()
else:
    with st.expander("업무 바로가기", expanded=False):
        _render_menu()

selected_menu = st.session_state.tradeflow_selected_menu
last_result = st.session_state.get("tradeflow_last_result")
pending = st.session_state.get("tradeflow_pending_confirmation")
show_task_panel = not isinstance(last_result, dict) and not isinstance(pending, dict)
if selected_menu == "1" and show_task_panel:
    _render_case_lookup()
elif selected_menu == "2" and show_task_panel:
    st.markdown(
        '<div class="kb-inline-note">오늘 기준 후보 거래를 선별해 '
        "Shipment → Financial → Critic 순서로 분석합니다.</div>",
        unsafe_allow_html=True,
    )
elif selected_menu == "3" and show_task_panel:
    _render_trade_upload()
elif selected_menu == "4" and show_task_panel:
    _render_calendar_upload()

if isinstance(last_result, dict):
    _show_result_actions(last_result)

if isinstance(pending, dict):
    _render_human_confirmation(pending)

auto_submit = bool(st.session_state.pop("tradeflow_auto_submit", False))
queued_question = str(st.session_state.get("tradeflow_queued_question") or "").strip()
if auto_submit and queued_question and not isinstance(pending, dict):
    st.session_state.tradeflow_queued_question = ""
    _submit_chat(
        queued_question,
        st.session_state.tradeflow_source_scope,
        st.session_state.tradeflow_case_hint,
    )

with st.container(key="chat-composer-shell"):
    prompt = st.chat_input(
        "메시지를 입력하세요…",
        key="chat-composer",
        disabled=isinstance(pending, dict) or not graph_connected,
    )
if isinstance(prompt, str) and prompt.strip():
    _submit_chat(
        prompt,
        st.session_state.tradeflow_source_scope,
        st.session_state.tradeflow_case_hint,
    )
