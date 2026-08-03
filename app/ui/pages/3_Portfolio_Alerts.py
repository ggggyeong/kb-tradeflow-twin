from __future__ import annotations

import streamlit as st

from app.ui import api_client
from app.ui.theme import api_guard, hero, setup_page

setup_page("Portfolio & Alerts", "🚦")
hero(
    "Portfolio Pulse",
    "Chat에서 실행한 금일 금융위험 분석 결과와 Alert를 확인합니다.",
    "Manual Risk Review",
)

st.info(
    "금일 금융위험 분석은 Chat의 **⚡ 금일 금융위험 거래 분석** 메뉴에서 "
    "사용자가 직접 실행합니다. 이 화면은 저장된 최신 결과를 조회만 합니다."
)

latest = api_guard(lambda: api_client.get("/api/monitor/reports/latest"))
daily_report = latest.get("report") if isinstance(latest, dict) else None
if isinstance(daily_report, dict):
    cols = st.columns(3)
    cols[0].metric("기준일", daily_report["as_of_date"])
    cols[1].metric("위험 거래", daily_report["risk_case_count"])
    cols[2].metric("최고 위험도", daily_report.get("highest_priority") or "없음")
    summary = daily_report.get("summary") or {}
    if isinstance(summary, dict) and summary.get("summary_text"):
        st.caption(str(summary["summary_text"]))
    st.link_button(
        "최신 금일 위험 보고서 열기",
        f"{api_client.API_BASE}{daily_report['pdf_url']}",
        use_container_width=True,
    )
else:
    st.caption("아직 Chat에서 생성된 금일 금융위험 보고서가 없습니다.")

alerts = api_guard(lambda: api_client.get("/api/alerts"))
if alerts is not None:
    st.markdown("### Alert Inbox")
    if not alerts:
        st.info("현재 Alert가 없습니다.")
    for alert in alerts:
        st.markdown(
            f'<div class="kb-card"><span class="kb-chip">{alert["severity"]}</span>'
            f'<span class="kb-chip">{alert["case_id"]}</span>'
            f"<h3>{alert['signal_code']}</h3>"
            f"<p>{alert['evidence'].get('guardrail', '')}</p></div>",
            unsafe_allow_html=True,
        )
