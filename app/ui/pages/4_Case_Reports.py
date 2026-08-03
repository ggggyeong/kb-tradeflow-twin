from __future__ import annotations

import streamlit as st

from app.ui import api_client
from app.ui.theme import api_guard, hero, setup_page

setup_page("Case & Reports", "📊")
hero("Case Studio", "planned/actual, finance, scenario와 audience별 리포트.", "Case Twin")

case_id = st.text_input("Case ID", "TRD-DEMO-003")
if st.button("Case Snapshot 조회", type="primary"):
    result = api_guard(lambda: api_client.get(f"/api/cases/{case_id}"))
    if result:
        st.session_state["case_snapshot"] = result
snapshot = st.session_state.get("case_snapshot")
if snapshot:
    st.json(snapshot)

st.markdown("### Shipment → Financial 지연 검토")
reported_at = st.date_input("제보일")
delay = st.number_input("ETD 기준 예상 지연일", min_value=0, max_value=365, value=9)
anchor_choice = st.selectbox(
    "지급 기준일 확인 (필요한 경우)",
    ["자동 확인", "B/L_DATE", "ON_BOARD_DATE"],
)
if st.button("지연 영향 계산"):
    result = api_guard(
        lambda: api_client.post(
            f"/api/cases/{case_id}/delay-advisory",
            {
                "reported_at": reported_at.isoformat(),
                "expected_delay_days": [int(delay)],
                "confirmed_anchor": (None if anchor_choice == "자동 확인" else anchor_choice),
            },
        )
    )
    if result:
        st.info(result["status"])
        st.json(result)

st.markdown("### 생성된 보고서")
st.caption("고객용·RM용 보고서는 Chat에서 생성 동의를 받은 뒤 동일한 근거로 생성됩니다.")
customer_col, rm_col = st.columns(2)
with customer_col:
    st.link_button(
        "고객용 PDF",
        f"{api_client.API_BASE}/api/reports/customer/{case_id}",
        use_container_width=True,
    )
with rm_col:
    st.link_button(
        "RM용 PDF",
        f"{api_client.API_BASE}/api/reports/rm/{case_id}",
        use_container_width=True,
    )
