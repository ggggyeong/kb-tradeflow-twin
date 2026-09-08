from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.portfolio import (
    PortfolioCitation,
    PortfolioConflictResult,
    PortfolioDocumentResult,
    PortfolioExplanationPoint,
    PortfolioProductOption,
    PortfolioRunRequest,
    ReceiptResolution,
)
from app.services.financial_calendar import read_financial_calendar
from app.services.financial_conflict import classify_portfolio_conflicts
from app.services.financial_retrieval import FinancialRetrieval
from app.services.llm_controller import LLMError, RunModel
from app.services.receipt_date import resolve_receipt_date
from app.services.service_policy import POLICIES, AnswerSlot, get_answer_slots
from app.tools.agent_tools import function_tool, invoke, message, observe


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: dict[str, str | None]


class EvidenceExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(
        min_length=10,
        max_length=220,
        description="제공된 본문 하나가 직접 말하는 사실 하나를 조건·대상·예외를 유지해 한 문장으로 설명합니다. 직접 답할 수 없으면 null입니다.",
    )


def has_unresolved_list_reference(text: str) -> bool:
    """A standalone answer must not refer to a missing list of conditions."""
    return bool(re.search(r"(?:아래|다음|위)\s*(?:의\s*)?각\s*호", text))


def slot_evidence(evidence: list[dict[str, Any]], slot: AnswerSlot) -> list[dict[str, Any]]:
    """Question-specific retrieval candidates, not fixed pages or answer spans."""
    return [
        hit
        for hit in evidence
        if set(hit.get("query_ids", [])) & set(slot.query_ids) and 12 <= len(hit["excerpt"]) <= 1200
    ]


def selection_context(
    evidence: list[dict[str, Any]], slots: Sequence[AnswerSlot]
) -> dict[str, Any]:
    """Only source selection is performed in this multi-candidate context."""
    return {
        "questions": [
            {
                "slot_id": slot.slot_id,
                "question": slot.title,
                "evidence_ids": [hit["citation_id"] for hit in slot_evidence(evidence, slot)],
            }
            for slot in slots
        ],
        "evidence": [
            {
                key: hit[key]
                for key in (
                    "citation_id",
                    "event_id",
                    "product_id",
                    "product_name",
                    "financial_institution",
                    "page",
                    "query_ids",
                    "excerpt",
                )
                if key in hit
            }
            for hit in evidence
        ],
    }


def selection_tool(evidence: list[dict[str, Any]], slots: Sequence[AnswerSlot]) -> dict[str, Any]:
    """The model chooses a retrieved ID or abstains; it cannot draft prose here."""
    tool = function_tool(
        "choose_financial_evidence",
        "각 핵심 질문에 직접 답하는 검색 본문 ID 하나를 선택하거나 null을 제출합니다.",
        EvidenceSelection,
    )
    fields: dict[str, Any] = {}
    for slot in slots:
        ids = [hit["citation_id"] for hit in slot_evidence(evidence, slot)]
        fields[slot.slot_id] = (
            {"anyOf": [{"type": "string", "enum": ids}, {"type": "null"}]}
            if ids
            else {"type": "null"}
        )
    tool["parameters"]["properties"]["selections"] = {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }
    return tool


class FinanceAdvisorAgent:
    """Compare tool -> filtered retrieval tool -> source-ID-validated RAG generation."""

    def __init__(self, *, retrieval: FinancialRetrieval | None = None) -> None:
        self.retrieval = retrieval or FinancialRetrieval()

    def prepare_inputs(
        self,
        request: PortfolioRunRequest,
        documents: list[PortfolioDocumentResult],
        *,
        model: RunModel,
    ) -> tuple[PortfolioRunRequest, ReceiptResolution, list[str]]:
        references: dict[str, str] = {}
        warnings: list[str] = []
        events = request.financial_events
        if request.financial_calendar_path:
            messages = [message({"transaction_id": request.transaction_id})]
            call = invoke(
                model,
                "finance",
                messages,
                function_tool(
                    "read_financial_calendar",
                    "서버의 엑셀에서 선택한 거래의 금융일정과 문서 번호를 검증합니다.",
                ),
            )
            calendar = read_financial_calendar(
                request.financial_calendar_path, request.transaction_id or ""
            )
            events, references, warnings = (
                calendar.events,
                calendar.document_references,
                calendar.warnings,
            )
            observe(
                model,
                messages,
                call,
                {
                    "event_count": len(events),
                    "document_reference_count": len(references),
                    "warnings": warnings,
                },
            )
        receipt = resolve_receipt_date(request, documents, references)
        if receipt.status == "REVIEW_REQUIRED":
            warnings.append(receipt.basis)
        currencies = {str(d.fields["currency"]) for d in documents if d.fields.get("currency")}
        currency = request.receipt_currency
        if not currency and receipt.document_links_verified and len(currencies) == 1:
            currency = next(iter(currencies))
        effective = request.model_copy(
            update={
                "financial_calendar_path": None,
                "financial_events": events,
                "expected_receipt_date": receipt.expected_receipt_date,
                "receipt_currency": currency,
            }
        )
        return effective, receipt, warnings

    def run(
        self,
        request: PortfolioRunRequest,
        documents: list[PortfolioDocumentResult],
        *,
        model: RunModel,
    ) -> tuple[list[PortfolioConflictResult], list[PortfolioProductOption], list[str]]:
        messages = [
            message(
                {
                    "trade_direction": request.trade_direction,
                    "expected_receipt_date": str(request.expected_receipt_date),
                    "document_statuses": [d.status for d in documents],
                }
            )
        ]
        call = invoke(
            model,
            "finance",
            messages,
            function_tool(
                "compare_financial_dates", "서버의 확인된 금융일정을 날짜 규칙으로 비교합니다."
            ),
        )
        conflicts = classify_portfolio_conflicts(
            request.expected_receipt_date,
            request.financial_events,
            receipt_currency=request.receipt_currency,
        )
        warnings = []
        currencies = {str(d.fields["currency"]) for d in documents if d.fields.get("currency")}
        if len(currencies) > 1 or (
            currencies and request.receipt_currency and currencies != {request.receipt_currency}
        ):
            warnings.append(
                "문서 통화와 입력 통화가 상충합니다. 외화 일정 판단과 상품 연결을 보류합니다."
            )
            for item in conflicts:
                if item.scenario_code == "FX_FORWARD_MATURITY" and item.status == "CONFLICT":
                    item.status, item.reason = "REVIEW_REQUIRED", warnings[-1]
        observe(model, messages, call, [item.model_dump(mode="json") for item in conflicts])
        if not any(item.status == "CONFLICT" for item in conflicts):
            return conflicts, [], warnings
        call = invoke(
            model,
            "finance",
            messages,
            function_tool(
                "search_financial_documents",
                "확인된 충돌과 거래 조건에 맞는 검토 완료 PDF만 검색합니다.",
            ),
        )
        try:
            evidence, search_warnings = self.retrieval.search(request, conflicts)
        except Exception:
            evidence, search_warnings = (
                [],
                ["금융자료 검색 실패: 검토 목록·PDF·Chroma 색인을 확인해 주세요."],
            )
        warnings.extend(search_warnings)
        observe(
            model, messages, call, {"evidence_count": len(evidence), "warnings": search_warnings}
        )
        if not evidence:
            return conflicts, [], warnings
        options: list[PortfolioProductOption] = []
        by_event = {c.event_id: c for c in conflicts if c.status == "CONFLICT"}
        grouped: dict[tuple[str, str], list[PortfolioExplanationPoint]] = {}
        source_hits: dict[tuple[str, str], dict[str, Any]] = {}
        for conflict in conflicts:
            if conflict.status != "CONFLICT":
                continue
            event_evidence = [hit for hit in evidence if hit["event_id"] == conflict.event_id]
            slots = get_answer_slots(
                conflict.scenario_code,
                export_receivable_confirmed=(
                    request.trade_direction == "EXPORT" and request.export_receivable_confirmed
                ),
            )
            if not event_evidence:
                warnings.extend(
                    f"{conflict.event_id}: '{slot.title}'는 검색 근거로 확인하지 못했습니다."
                    for slot in slots
                )
                continue
            selection_messages = [
                message(
                    {
                        "conflict": conflict.model_dump(mode="json"),
                        **selection_context(event_evidence, slots),
                    }
                )
            ]
            try:
                call = invoke(
                    model, "finance", selection_messages, selection_tool(event_evidence, slots)
                )
            except LLMError:
                warnings.append(
                    f"{conflict.event_id}: 근거 선택 호출이 실패해 해당 충돌 설명을 보류했습니다."
                )
                continue
            try:
                selection = EvidenceSelection.model_validate(call.arguments)
            except ValidationError:
                warnings.append(
                    f"{conflict.event_id}: 근거 선택 응답 형식이 잘못되어 해당 충돌 설명을 보류했습니다."
                )
                observe(model, selection_messages, call, {"accepted_selection_count": 0})
                model.audit[-1].status = "REJECTED"
                model.audit[-1].message = "근거 선택 응답 형식 검증 실패"
                continue
            if set(selection.selections) != {slot.slot_id for slot in slots}:
                warnings.append(
                    f"{conflict.event_id}: 요청한 질문과 다른 근거 선택 형식을 제외했습니다."
                )
                observe(model, selection_messages, call, {"accepted_selection_count": 0})
                continue
            observe(
                model,
                selection_messages,
                call,
                {"selected_count": sum(ref is not None for ref in selection.selections.values())},
            )
            for slot in slots:
                evidence_id = selection.selections[slot.slot_id]
                candidates = {
                    hit["citation_id"]: hit for hit in slot_evidence(event_evidence, slot)
                }
                if evidence_id is None:
                    warnings.append(
                        f"{conflict.event_id}: '{slot.title}'는 검색 근거로 확인하지 못했습니다."
                    )
                    continue
                hit = candidates.get(evidence_id)
                if hit is None:
                    warnings.append(
                        f"{conflict.event_id}: 질문에 연결되지 않은 근거 ID의 설명을 제외했습니다."
                    )
                    continue
                # One question and one pinned paragraph: no source IDs, other
                # candidates, prior draft or earlier messages enter this call.
                explanation_messages = [
                    message({"question": slot.title, "source_text": hit["excerpt"]})
                ]
                try:
                    explanation_call = invoke(
                        model,
                        "finance_explain",
                        explanation_messages,
                        function_tool(
                            "explain_financial_evidence",
                            "제공된 단일 본문으로 질문에 답하거나 직접 근거가 없으면 null을 제출합니다.",
                            EvidenceExplanation,
                        ),
                    )
                except LLMError:
                    warnings.append(
                        f"{conflict.event_id}: '{slot.title}'의 설명 생성 호출이 실패해 해당 답변을 보류했습니다."
                    )
                    continue
                try:
                    explanation = EvidenceExplanation.model_validate(explanation_call.arguments)
                except ValidationError:
                    warnings.append(
                        f"{conflict.event_id}: '{slot.title}'의 설명 응답 형식이 잘못되어 해당 답변을 보류했습니다."
                    )
                    observe(
                        model, explanation_messages, explanation_call, {"answer_provided": False}
                    )
                    model.audit[-1].status = "REJECTED"
                    model.audit[-1].message = "설명 응답 형식 검증 실패"
                    continue
                if explanation.text is None:
                    warnings.append(
                        f"{conflict.event_id}: '{slot.title}'는 선택된 본문에서 확인하지 못했습니다."
                    )
                    observe(
                        model, explanation_messages, explanation_call, {"answer_provided": False}
                    )
                    continue
                if has_unresolved_list_reference(explanation.text):
                    warnings.append(
                        f"{conflict.event_id}: '{slot.title}'의 설명이 원문 목록에 의존하여 단독으로 이해하기 어려워 해당 답변을 보류했습니다."
                    )
                    observe(
                        model, explanation_messages, explanation_call, {"answer_provided": False}
                    )
                    model.audit[-1].status = "REJECTED"
                    model.audit[-1].message = "설명에 독립적으로 확인할 수 없는 목록 참조가 남음"
                    continue
                # Identity checks establish provenance, not semantic accuracy.
                citation = PortfolioCitation(
                    source_file=hit["source_file"],
                    page=hit["page"],
                    source_sha256=hit["source_sha256"],
                    chunk_id=hit["chunk_id"],
                    excerpt=hit["excerpt"],
                    topic=hit.get("topic"),
                    section_id=hit.get("section_id"),
                    citation_id=hit["citation_id"],
                    source_url=hit.get("source_url"),
                )
                key = (conflict.event_id, hit["product_id"])
                grouped.setdefault(key, []).append(
                    PortfolioExplanationPoint(
                        text=explanation.text,
                        supporting_quote=hit["excerpt"],
                        citations=[citation],
                        slot_id=slot.slot_id,
                        question=slot.title,
                    )
                )
                source_hits[key] = hit
                observe(model, explanation_messages, explanation_call, {"answer_provided": True})
        for (event_id, product_id), points in grouped.items():
            conflict = by_event[event_id]
            first_hit = source_hits[(event_id, product_id)]
            citations = {p.citations[0].citation_id: p.citations[0] for p in points}
            options.append(
                PortfolioProductOption(
                    event_id=event_id,
                    scenario_code=conflict.scenario_code,
                    product_id=product_id,
                    product_name=first_hit["product_name"],
                    financial_institution=first_hit.get("financial_institution"),
                    why_consider=POLICIES[conflict.scenario_code].customer_need,
                    supporting_quote=points[0].supporting_quote,
                    explanation_points=points,
                    conditions_to_check=list(POLICIES[conflict.scenario_code].questions),
                    citations=list(citations.values()),
                )
            )
        covered = {option.event_id for option in options}
        warnings.extend(
            f"{c.event_id}: 제공 가능한 금융 설명이 없습니다. 검색·근거 선택·설명 생성 단계의 확인사항을 살펴봐 주세요."
            for c in conflicts
            if c.status == "CONFLICT" and c.event_id not in covered
        )
        return conflicts, options, list(dict.fromkeys(warnings))
