from __future__ import annotations

from typing import Any

from app.schemas.orchestration import ToolName
from app.tools.agent_tools import function_tool

DESCRIPTIONS: dict[ToolName, str] = {
    "call_document_agent": "문서 Agent가 PDF/OCR 해석과 필드 검증을 수행합니다.",
    "call_finance_agent": "금융 Agent가 일정 비교 도구와 RAG 도구로 근거 있는 검토 정보를 제공합니다.",
    "generate_report": "앞 단계의 결과와 미확인 사항을 담당자 검토용 PDF로 저장합니다.",
}


def tool_definition(name: ToolName) -> dict[str, Any]:
    return function_tool(name, DESCRIPTIONS[name])
