from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.portfolio import (
    PortfolioCitation,
    PortfolioConflictResult,
    PortfolioDocumentResult,
    PortfolioProductOption,
    PortfolioRunRequest,
    ReceiptResolution,
)
from app.services.financial_calendar import read_financial_calendar
from app.services.financial_conflict import classify_portfolio_conflicts
from app.services.financial_retrieval import FinancialRetrieval
from app.services.llm_controller import RunModel
from app.services.receipt_date import resolve_receipt_date
from app.services.service_policy import POLICIES
from app.tools.agent_tools import function_tool, invoke, message, observe


class ReviewOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    product_id: str
    supporting_quote: str | None = Field(
        min_length=20,
        max_length=240,
        description="이 자료의 excerpt 하나에서 핵심 조건·의무 문장을 20~240자 그대로 복사합니다. 요약·의역·다른 자료의 문장 금지. 관련 문장이 없으면 null과 경고를 제출합니다.",
    )
    citation_ids: list[str] = Field(min_length=1, max_length=3)


class FinancialSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: list[ReviewOption] = Field(max_length=9)
    warnings: list[str] = Field(max_length=6)


def review_context(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep full source text, but send repeated provenance metadata only once."""
    candidates = {}
    for hit in evidence:
        candidates[(hit["event_id"], hit["product_id"])] = {
            key: hit[key]
            for key in (
                "event_id",
                "product_id",
                "product_name",
                "financial_institution",
                "selection_reason",
                "service_code",
                "service_title",
                "customer_need",
            )
            if key in hit
        }
    return {
        "candidates": list(candidates.values()),
        "evidence": [
            {
                key: hit[key]
                for key in ("citation_id", "event_id", "product_id", "page", "topic", "excerpt")
                if key in hit
            }
            for hit in evidence
        ],
    }


def review_tool(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Allow only retrieved event/product/reference combinations at the API boundary."""
    tool = function_tool(
        "submit_financial_review",
        "자료마다 핵심 원문 또는 null을 출처 ID와 제출합니다. 검토 목적·질문은 서버가 작성합니다.",
        FinancialSubmission,
    )
    groups: dict[tuple[str, str], list[str]] = {}
    for hit in evidence:
        groups.setdefault((hit["event_id"], hit["product_id"]), []).append(hit["citation_id"])
    variants = []
    for (event_id, product_id), refs in groups.items():
        variant = deepcopy(tool["parameters"]["$defs"]["ReviewOption"])
        fields = variant["properties"]
        fields["event_id"]["enum"] = [event_id]
        fields["product_id"]["enum"] = [product_id]
        fields["citation_ids"]["items"]["enum"] = refs
        # All selected topic spans for this source travel together. A risk-only
        # citation must not be displayed as evidence of unrelated settlement terms.
        fields["citation_ids"]["minItems"] = len(refs)
        fields["citation_ids"]["maxItems"] = len(refs)
        variants.append(variant)
    tool["parameters"]["$defs"]["ReviewOption"] = {"anyOf": variants}
    tool["parameters"]["properties"]["options"]["minItems"] = len(groups)
    tool["parameters"]["properties"]["options"]["maxItems"] = len(groups)
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
        observe(model, messages, call, {**review_context(evidence), "warnings": search_warnings})
        if not evidence:
            return conflicts, [], warnings
        call = invoke(
            model,
            "finance",
            messages,
            review_tool(evidence),
        )
        submission = FinancialSubmission.model_validate(call.arguments)
        by_id = {hit["citation_id"]: hit for hit in evidence}
        by_event = {c.event_id: c for c in conflicts if c.status == "CONFLICT"}
        options: list[PortfolioProductOption] = []
        counts: Counter[str] = Counter()
        seen: set[tuple[str, str]] = set()
        for option in submission.options:
            conflict = by_event.get(option.event_id)
            hits = [by_id.get(ref) for ref in option.citation_ids]
            if conflict is None or any(
                hit is None
                or hit["event_id"] != option.event_id
                or hit["product_id"] != option.product_id
                for hit in hits
            ):
                warnings.append(
                    "존재하지 않거나 다른 거래·상품에 속한 출처가 있어 해당 설명을 제외했습니다."
                )
                continue
            if (option.event_id, option.product_id) in seen or counts[option.event_id] >= 3:
                continue
            concrete: list[dict[str, Any]] = [hit for hit in hits if hit is not None]
            required_refs = {
                hit["citation_id"]
                for hit in evidence
                if hit["event_id"] == option.event_id and hit["product_id"] == option.product_id
            }
            if set(option.citation_ids) != required_refs:
                warnings.append(
                    f"{option.event_id}: 해당 자료의 필수 검색 근거가 빠져 설명을 제외했습니다."
                )
                continue
            if not option.supporting_quote or not any(
                option.supporting_quote in hit["excerpt"] for hit in concrete
            ):
                warnings.append(
                    f"{option.event_id}: 원문과 일치하지 않는 인용 문장이 있어 설명을 제외했습니다."
                )
                continue
            counts[option.event_id] += 1
            seen.add((option.event_id, option.product_id))
            options.append(
                PortfolioProductOption(
                    event_id=option.event_id,
                    scenario_code=conflict.scenario_code,
                    product_id=option.product_id,
                    product_name=concrete[0]["product_name"],
                    financial_institution=concrete[0].get("financial_institution"),
                    why_consider=POLICIES[conflict.scenario_code].customer_need,
                    supporting_quote=option.supporting_quote,
                    conditions_to_check=list(POLICIES[conflict.scenario_code].questions),
                    citations=[
                        PortfolioCitation(
                            source_file=hit["source_file"],
                            page=hit["page"],
                            source_sha256=hit["source_sha256"],
                            chunk_id=hit["chunk_id"],
                            excerpt=hit["excerpt"],
                            topic=hit.get("topic"),
                            section_id=hit.get("section_id"),
                        )
                        for hit in concrete
                    ],
                )
            )
        warnings.extend(submission.warnings)
        for event_id, product_id in dict.fromkeys(
            (h["event_id"], h["product_id"]) for h in evidence
        ):
            if (event_id, product_id) not in seen:
                warnings.append(
                    f"{event_id}: {product_id}의 원문 선택이 누락되거나 검증되지 않아 해당 자료를 보류했습니다."
                )
        covered = {option.event_id for option in options}
        warnings.extend(
            f"{c.event_id}: 관련 조건을 확인할 근거가 부족해 상품 정보를 제시하지 않았습니다."
            for c in conflicts
            if c.status == "CONFLICT" and c.event_id not in covered
        )
        observe(model, messages, call, {"accepted_option_count": len(options)})
        return conflicts, options, list(dict.fromkeys(warnings))
