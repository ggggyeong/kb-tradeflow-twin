from __future__ import annotations

import streamlit as st

from app.ui import api_client
from app.ui.theme import api_guard, hero, setup_page

setup_page("Command Center", "🟢")
hero(
    "TradeFlow Command Center",
    "문서에서 상담 연결까지, 확인 가능한 근거와 안전선을 한 흐름으로.",
    "KB Trade Operations",
)

health = api_guard(lambda: api_client.get("/health"))
if health:
    cols = st.columns(4)
    cols[0].metric("API", health["status"].upper())
    cols[1].metric("실행 모드", health["mode"].upper())
    cols[2].metric("업무 기준일", health["virtual_date"])
    cols[3].metric("Build", health["version"])

st.markdown("### 네 개의 Live 업무 흐름")
cards = [
    (
        "01",
        "8 PDF 거래 구성",
        "Booking·Invoice·B/L → TRD-001~003 → 정상·필드 확인·B/L 대기",
    ),
    (
        "02-1",
        "선제 모니터링",
        "금융일정 XLSX 후보 → 선적 상태 → 충돌 우선순위 Alert",
    ),
    (
        "02-2",
        "지연 영향 분석",
        "사용자 제보 지연일 → 예상 회수일 → 금융일정 충돌 계산",
    ),
    (
        "03",
        "상품 근거·브리핑",
        "KB 상품 PDF 페이지 근거 → 동의 후 고객용·KB 직원용 PDF",
    ),
]
for left, right in zip(cards[::2], cards[1::2], strict=True):
    col1, col2 = st.columns(2)
    for col, item in [(col1, left), (col2, right)]:
        with col:
            st.markdown(
                f'<div class="kb-card"><span class="kb-chip">{item[0]}</span>'
                f"<h3>{item[1]}</h3><p>{item[2]}</p></div>",
                unsafe_allow_html=True,
            )

st.info("왼쪽 페이지 메뉴에서 각 업무 화면으로 이동하세요.")
