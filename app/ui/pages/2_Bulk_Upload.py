from __future__ import annotations

import uuid

import streamlit as st

from app.ui import api_client
from app.ui.theme import api_guard, hero, setup_page

setup_page("Bulk Upload", "📥")
hero("Bulk Trade Intake", "무역 PDF와 금융일정 XLSX를 각각의 Agent로 등록합니다.", "Data Steward")

st.subheader("무역 문서")
batch_id = st.text_input("Batch ID", f"UI-{uuid.uuid4().hex[:8]}")
files = st.file_uploader(
    "Booking·Invoice·B/L PDF 선택",
    type=["pdf"],
    accept_multiple_files=True,
    key="trade-document-files",
)
col1, col2 = st.columns(2)
if col1.button("1. 업로드 · 분석", type="primary", use_container_width=True):
    uploaded = api_guard(lambda: api_client.upload(batch_id, files))
    if uploaded:
        result = api_guard(lambda: api_client.post(f"/api/batches/{batch_id}/analyze"))
        if result:
            st.session_state["batch_analysis"] = result
if col2.button("2. 확인된 Case Commit", use_container_width=True):
    result = api_guard(
        lambda: api_client.post(f"/api/batches/{batch_id}/commit", {"confirmed_case_ids": []})
    )
    if result:
        st.success(
            f"Commit {len(result['committed_case_ids'])} · "
            f"Pending {len(result['pending_case_ids'])}"
        )
        st.json(result)

analysis = st.session_state.get("batch_analysis")
if analysis:
    summary = analysis["summary"]
    metrics = st.columns(4)
    metrics[0].metric("Files", summary["file_count"])
    metrics[1].metric("Cases", summary["case_count"])
    metrics[2].metric("Unmatched", len(summary["unmatched_document_ids"]))
    metrics[3].metric("Type confirm", len(summary["type_confirmation_document_ids"]))
    for request in summary.get("human_requests", []):
        with st.expander(
            f"확인 필요 · {request['case_id']} · {request['field_path']}",
            expanded=True,
        ):
            st.write(request["prompt"])
            override_value = st.text_input(
                "확인된 값",
                key=f"override-value-{request['request_id']}",
            )
            override_reason = st.text_input(
                "확인 근거 (선택)",
                key=f"override-reason-{request['request_id']}",
            )
            if st.button(
                "수기 확인값 반영",
                key=f"override-submit-{request['request_id']}",
                disabled=not override_value.strip(),
            ):
                corrected = api_guard(
                    lambda request=request, value=override_value, reason=override_reason: (
                        api_client.post(
                            f"/api/batches/{batch_id}/field-overrides",
                            {
                                "document_id": request["document_id"],
                                "exact_standard_field": request["exact_standard_field"],
                                "value": value,
                                "actor": "STREAMLIT_USER",
                                "reason": reason or None,
                            },
                        )
                    )
                )
                if corrected:
                    st.session_state["batch_analysis"] = corrected
                    st.rerun()
    for issue in summary.get("missing_document_issues", []):
        st.info(f"{issue['case_id']} · {issue['message']}")
    for case in summary["cases"]:
        with st.expander(f"{case['case_id']} · {case['status']}", expanded=True):
            st.write("누락 문서", case["missing_documents"] or "없음")
            st.write("누락 Core", case["missing_core_fields"] or "없음")
            st.json(case["match_scores"])

st.divider()
st.subheader("금융일정 2-Sheet XLSX")
st.caption("1.금융이벤트 · 2.거래연결 (회사 ID는 로그인 세션에서 적용)")
if "tradeflow_company_id" not in st.session_state:
    st.session_state.tradeflow_company_id = "DEMO-A"
st.text_input(
    "로그인 회사 ID",
    key="tradeflow_company_id",
    help="XLSX에는 company_id를 넣지 않습니다. 현재 세션의 회사 ID를 사용합니다.",
)
calendar_batch_id = st.text_input(
    "금융일정 Batch ID",
    f"FIN-CAL-{uuid.uuid4().hex[:8]}",
    key="financial-calendar-batch-id",
)
calendar_file = st.file_uploader(
    "2-Sheet 금융일정 XLSX 1개",
    type=["xlsx"],
    accept_multiple_files=False,
    key="financial-calendar-file",
)
if st.button(
    "금융일정 XLSX 업로드",
    type="primary",
    use_container_width=True,
    disabled=calendar_file is None,
):
    uploaded = api_guard(
        lambda: api_client.upload(
            calendar_batch_id,
            [calendar_file] if calendar_file is not None else [],
        )
    )
    if uploaded:
        st.session_state["financial_calendar_batch_id"] = calendar_batch_id
        st.success(
            "업로드가 완료되었습니다. Chat의 '준비 · 금융일정 XLSX 반영'을 실행하면 "
            "2-Sheet 검증 후 DB에 반영됩니다."
        )
        st.code(
            f"company_id={st.session_state.tradeflow_company_id}, "
            f"batch_id={calendar_batch_id}에 업로드한 2-Sheet 금융일정 XLSX를 "
            "검증하고 로그인 회사의 기존 거래에 금융 이벤트와 연결 정보를 반영해줘."
        )
