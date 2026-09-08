from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from app.schemas.document import DocumentType, LayoutDocument
from app.schemas.portfolio import PortfolioDocumentResult
from app.services.ingestion.document_analyzer import CORE_FIELDS_BY_DOCUMENT, _parse_date
from app.services.ingestion.ocr_backend import NativePdfLayoutBackend, OcrBackend, PaddleOcrBackend
from app.services.llm_controller import RunModel
from app.tools.agent_tools import function_tool, invoke, message, observe


class FieldProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal[
        "booking_no",
        "etd",
        "invoice_no",
        "invoice_date",
        "total_amount",
        "currency",
        "bl_no",
        "on_board_date",
        "payment_terms",
    ]
    raw_value: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=1)


class DocumentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    document_type: Literal[
        "BOOKING_CONFIRMATION", "COMMERCIAL_INVOICE", "BILL_OF_LADING", "UNKNOWN"
    ]
    fields: list[FieldProposal] = Field(max_length=9)


class DocumentSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[DocumentProposal] = Field(max_length=3)


def _normalize_field(key: str, raw: str) -> str:
    if key in {"etd", "invoice_date", "on_board_date"}:
        return date.fromisoformat(_parse_date(raw)).isoformat()
    if key == "currency":
        value = raw.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("ambiguous currency")
        return value
    if key == "total_amount":
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", raw):
            raise ValueError("ambiguous amount")
        amount = Decimal(raw.replace(",", ""))
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid amount")
        return str(amount)
    return raw


class DocumentAgent:
    """LLM interprets page text; a validation tool accepts only literal source spans."""

    name = "document_agent"

    def __init__(self, *, backend: OcrBackend | None = None) -> None:
        self.backend = backend

    def run(self, paths: Sequence[Path], *, model: RunModel) -> list[PortfolioDocumentResult]:
        messages = [message({"document_count": len(paths)})]
        read_call = invoke(
            model,
            "document",
            messages,
            function_tool(
                "read_pdf", "서버 PDF의 페이지 텍스트를 읽습니다. 이미지 PDF는 OCR로 처리합니다."
            ),
        )
        layouts: dict[str, LayoutDocument] = {}
        results: dict[str, PortfolioDocumentResult] = {}
        pages_for_model = []
        total_chars = 0
        for index, path in enumerate(paths):
            doc_id = f"doc-{index + 1}"
            result = PortfolioDocumentResult(
                file_name=unicodedata.normalize("NFC", path.name),
                document_type="UNKNOWN",
                source_sha256="",
                status="REVIEW_REQUIRED",
            )
            results[doc_id] = result
            try:
                if path.suffix.lower() != ".pdf" or path.stat().st_size > 20_000_000:
                    raise ValueError("unsupported PDF")
                if len(PdfReader(path).pages) > 20:
                    raise ValueError("page limit")
                result.source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                if self.backend:
                    layout = self.backend.extract(path)
                else:
                    try:
                        layout = NativePdfLayoutBackend().extract(path)
                    except ValueError:
                        try:
                            from app.services.ingestion.rapid_ocr_backend import RapidOcrBackend

                            layout = RapidOcrBackend().extract(path)
                        except ImportError:
                            layout = PaddleOcrBackend().extract(path)
                if len(layout.pages) > 20:
                    raise ValueError("page limit")
                total_chars += len(layout.text)
                if total_chars > 50_000:
                    raise ValueError("context limit")
                layouts[doc_id] = layout
                result.ocr_backend = layout.backend
                result.warnings.extend(layout.warnings)
                pages_for_model.append(
                    {
                        "document_id": doc_id,
                        "pages": [
                            {"page": p.page, "text": "\n".join(line.text for line in p.lines)}
                            for p in layout.pages
                        ],
                    }
                )
            except Exception:
                result.warnings.append(
                    "PDF 읽기/OCR 실패 또는 파일·페이지·텍스트 제한 초과. 원본 확인이 필요합니다."
                )
        observe(
            model,
            messages,
            read_call,
            {
                "documents": pages_for_model,
                "failed_document_ids": [key for key in results if key not in layouts],
            },
        )
        if not layouts:
            return list(results.values())
        call = invoke(
            model,
            "document",
            messages,
            function_tool(
                "validate_fields",
                "문서 종류와 원문 필드 후보를 검증합니다. 값은 원문을 그대로 복사하세요.",
                DocumentSubmission,
            ),
        )
        submission = DocumentSubmission.model_validate(call.arguments)
        proposals = {item.document_id: item for item in submission.documents}
        if len(proposals) != len(submission.documents) or set(proposals) - layouts.keys():
            raise ValueError("Unknown or duplicate document id")
        for doc_id, layout in layouts.items():
            result = results[doc_id]
            proposal = proposals.get(doc_id)
            if proposal is None or proposal.document_type == "UNKNOWN":
                result.warnings.append("문서 종류 미확인 또는 추출 결과 누락")
                continue
            result.document_type = proposal.document_type
            required = CORE_FIELDS_BY_DOCUMENT[DocumentType(proposal.document_type)]
            allowed = required | (
                {"payment_terms"} if proposal.document_type == "COMMERCIAL_INVOICE" else set()
            )
            duplicates = {
                x.field
                for x in proposal.fields
                if sum(y.field == x.field for y in proposal.fields) > 1
            }
            for field in proposal.fields:
                if field.field in duplicates:
                    result.warnings.append(f"중복·상충 필드: {field.field}")
                    continue
                page = next((p for p in layout.pages if p.page == field.page), None)
                matching = (
                    [line for line in page.lines if field.raw_value in line.text] if page else []
                )
                if field.field not in allowed or not matching:
                    result.warnings.append(f"원문 근거를 검증할 수 없는 필드: {field.field}")
                    continue
                try:
                    value = _normalize_field(field.field, field.raw_value)
                except (ValueError, InvalidOperation):
                    result.warnings.append(f"날짜·금액·통화 형식 확인 필요: {field.field}")
                    continue
                line = matching[0]
                if len(matching) > 1 or line.confidence < 0.8:
                    result.warnings.append(f"중복 원문 또는 낮은 OCR 신뢰도: {field.field}")
                result.fields[field.field] = value
                result.evidence.append(
                    {
                        "field": field.field,
                        "page": field.page,
                        "raw_value": field.raw_value,
                        "source_line": line.text,
                        "ambiguous": len(matching) > 1,
                        "bbox": line.bbox,
                        "confidence": line.confidence,
                    }
                )
            missing = required - result.fields.keys()
            if missing:
                result.warnings.append("핵심 필드 누락: " + ", ".join(sorted(missing)))
            result.status = (
                "REVIEW_REQUIRED"
                if missing or len(result.warnings) > len(layout.warnings)
                else "ANALYZED"
            )
        observe(model, messages, call, {"statuses": [x.status for x in results.values()]})
        return list(results.values())
