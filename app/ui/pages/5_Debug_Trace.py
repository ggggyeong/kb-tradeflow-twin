from __future__ import annotations

import streamlit as st

from app.ui import api_client
from app.ui.theme import api_guard, hero, setup_page

setup_page("Debug Trace", "🔎")
hero("Debug Trace", "숨겨진 reasoning 없이 node·tool·I/O 요약만 표시합니다.", "Observability")

st.caption(
    "Chat 또는 Monitoring 응답의 `request_id`를 입력하면 같은 실행의 Node·Tool 근거를 조회합니다."
)
request_id = st.text_input("Request ID", placeholder="예: REQ-7f31a5c2e9d84b60")
if st.button("Trace 조회", type="primary", disabled=not request_id.strip()):
    result = api_guard(lambda: api_client.get(f"/api/debug/runs/{request_id}"))
    if result:
        if result["langsmith_url"]:
            st.link_button("LangSmith Trace 열기", result["langsmith_url"])
        for index, event in enumerate(result["events"], start=1):
            with st.expander(f"{index}. {event['span_type']} · {event['name']}", True):
                st.write(f"{event['timing_ms']} ms")
                st.json({"input": event["input"], "output": event["output"]})
