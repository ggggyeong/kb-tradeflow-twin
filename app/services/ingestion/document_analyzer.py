"""OCR-backed extraction for the three portfolio trade documents."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.schemas.document import (
    DocumentAnalysis,
    DocumentType,
    ExtractedField,
    LayoutDocument,
)
from app.services.ingestion.ocr_backend import (
    LabelValueEvidence,
    LayoutDocumentIndex,
    OcrBackend,
    default_ocr_backend,
)
from app.services.ingestion.template_classifier import classify_trade_document


@dataclass(frozen=True)
class CoreFieldSpec:
    """The intentionally small field contract used by the portfolio story."""

    key: str
    doc_type: DocumentType


CORE_FIELD_SPECS = (
    CoreFieldSpec("booking_no", DocumentType.BOOKING_CONFIRMATION),
    CoreFieldSpec("etd", DocumentType.BOOKING_CONFIRMATION),
    CoreFieldSpec("invoice_no", DocumentType.COMMERCIAL_INVOICE),
    CoreFieldSpec("invoice_date", DocumentType.COMMERCIAL_INVOICE),
    CoreFieldSpec("total_amount", DocumentType.COMMERCIAL_INVOICE),
    CoreFieldSpec("currency", DocumentType.COMMERCIAL_INVOICE),
    CoreFieldSpec("bl_no", DocumentType.BILL_OF_LADING),
    CoreFieldSpec("on_board_date", DocumentType.BILL_OF_LADING),
)

CORE_FIELDS_BY_DOCUMENT: dict[DocumentType, frozenset[str]] = {
    doc_type: frozenset(spec.key for spec in CORE_FIELD_SPECS if spec.doc_type is doc_type)
    for doc_type in DocumentType
}


@dataclass(frozen=True)
class FieldCandidate:
    """A normalized value and the exact layout evidence used to derive it."""

    label: str
    raw_value: str
    value: Any
    page: int
    bbox: list[float]
    confidence: float

    def to_schema(self) -> ExtractedField:
        return ExtractedField(
            value=self.value,
            raw_value=self.raw_value,
            raw_label=self.label,
            page=self.page,
            bbox=self.bbox,
            confidence=self.confidence,
        )


def _parse_date(value: str) -> str:
    cleaned = " ".join(value.replace(".", " ").replace(",", " ").split())
    for fmt in ("%d%b%y", "%b %d %Y", "%d %b %Y", "%Y-%m-%d", "%Y %m %d"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _candidate(
    evidence: LabelValueEvidence | None,
    *,
    raw_value: str | None = None,
    value: Any | None = None,
    label: str | None = None,
) -> FieldCandidate | None:
    if evidence is None:
        return None
    raw = " ".join((raw_value if raw_value is not None else evidence.value).strip().split())
    if not raw:
        return None
    return FieldCandidate(
        label=label or evidence.label,
        raw_value=raw,
        value=value if value is not None else raw,
        page=evidence.page,
        bbox=evidence.bbox,
        confidence=evidence.confidence,
    )


def _put(
    output: dict[str, FieldCandidate],
    key: str,
    candidate: FieldCandidate | None,
) -> None:
    if candidate is not None:
        output[key] = candidate


def _booking(document: LayoutDocument) -> dict[str, FieldCandidate]:
    index = LayoutDocumentIndex(document)
    output: dict[str, FieldCandidate] = {}
    booking_number = index.find_value(("Booking No", "Booking Number"))
    if booking_number is not None:
        raw_number = re.split(
            r"\bBooking Ref(?:erence)?\b|\bBooking Date\b",
            booking_number.value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" :")
        _put(output, "booking_no", _candidate(booking_number, raw_value=raw_number))
    etd = index.find_value(("Proforma 1st vessel ETD", "Estimated Time of Departure", "ETD"))
    if etd is not None:
        _put(output, "etd", _candidate(etd, value=_parse_date(etd.value)))
    return output


def _invoice_number_and_date(value: str) -> tuple[str, str | None]:
    date_match = re.search(
        r"(?P<date>[A-Za-z]{3,9}\.?\s+\d{1,2}\.?\s+\d{4}|\d{4}[-./]\d{1,2}[-./]\d{1,2})",
        value,
    )
    if date_match is None:
        return value.strip(), None
    return value[: date_match.start()].strip(" :-"), date_match.group("date").strip()


def _currency_from_amount(value: str) -> str | None:
    upper = value.upper().replace(" ", "")
    if "US$" in upper or "USD" in upper:
        return "USD"
    if "EUR" in upper or "€" in value:
        return "EUR"
    if "JPY" in upper or "¥" in value:
        return "JPY"
    if "KRW" in upper or "₩" in value:
        return "KRW"
    return None


def _normalized_amount(value: str) -> str:
    matched = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    return matched.group(0).replace(",", "") if matched else value


def _invoice_total(value: str) -> str:
    """Prefer the final currency-denominated amount over quantity/unit-price numbers."""
    currency_amounts = re.findall(
        r"(?:US\$|USD|EUR|JPY|KRW|[$€¥₩])\s*\d[\d,]*(?:\.\d+)?",
        value,
        flags=re.IGNORECASE,
    )
    return currency_amounts[-1] if currency_amounts else value


def _currency_amount_evidence(index: LayoutDocumentIndex) -> LabelValueEvidence | None:
    pattern = re.compile(
        r"(?:US\$|USD|EUR|JPY|KRW|[$€¥₩])\s*\d[\d,]*(?:\.\d+)?",
        flags=re.IGNORECASE,
    )
    candidates = [line for line in index.lines if pattern.search(line.text)]
    if not candidates:
        return None
    line = candidates[-1]
    return LabelValueEvidence(
        label="Amount",
        value=line.text,
        page=line.page,
        bbox=list(line.bbox),
        confidence=line.confidence,
        direction="NEARBY_LINE",
    )


def _invoice(document: LayoutDocument) -> dict[str, FieldCandidate]:
    index = LayoutDocumentIndex(document)
    output: dict[str, FieldCandidate] = {}
    invoice = index.find_value(("Invoice No. and date", "Invoice Number and Date"))
    if invoice is not None:
        number, invoice_date = _invoice_number_and_date(invoice.value)
        _put(output, "invoice_no", _candidate(invoice, raw_value=number))
        if invoice_date:
            _put(
                output,
                "invoice_date",
                _candidate(
                    invoice,
                    raw_value=invoice_date,
                    value=_parse_date(invoice_date),
                    label="Invoice date",
                ),
            )

    amount = index.find_value(("Total Amount", "Invoice Total", "Amount"))
    if amount is None or not _currency_from_amount(amount.value):
        amount = _currency_amount_evidence(index)
    if amount is not None:
        raw_total = _invoice_total(amount.value)
        _put(
            output,
            "total_amount",
            _candidate(amount, raw_value=raw_total, value=_normalized_amount(raw_total)),
        )
        currency = _currency_from_amount(raw_total)
        if currency:
            _put(
                output,
                "currency",
                _candidate(
                    amount,
                    raw_value=currency,
                    value=currency,
                    label="Currency from amount",
                ),
            )
    if "currency" not in output:
        explicit_currency = index.find_value("Currency")
        if explicit_currency is not None:
            currency = _currency_from_amount(explicit_currency.value) or explicit_currency.value
            _put(
                output,
                "currency",
                _candidate(explicit_currency, raw_value=currency, value=currency),
            )
    return output


def _bill_of_lading(document: LayoutDocument) -> dict[str, FieldCandidate]:
    index = LayoutDocumentIndex(document)
    output: dict[str, FieldCandidate] = {}
    bill_number = index.find_value(("B/L No.", "Bill of Lading No."))
    if bill_number is not None:
        raw_number = re.split(
            r"[①-⑳]|\bConsignee\b|\bNotify Party\b",
            bill_number.value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ;:")
        _put(output, "bl_no", _candidate(bill_number, raw_value=raw_number))
    onboard = index.find_value(
        ("Laden on board vessel", "On Board Date"),
        below_index=1,
        allow_inline=True,
        max_vertical_gap=0.10,
    )
    if onboard is None:
        onboard = index.find_value("On Board Date")
    if onboard is not None:
        date_match = re.search(
            r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}[-./]\d{1,2}[-./]\d{1,2}",
            onboard.value,
        )
        if date_match is not None:
            raw_date = date_match.group(0)
            _put(
                output,
                "on_board_date",
                _candidate(onboard, raw_value=raw_date, value=_parse_date(raw_date)),
            )
    return output


EXTRACTORS: dict[DocumentType, Callable[[LayoutDocument], dict[str, FieldCandidate]]] = {
    DocumentType.BOOKING_CONFIRMATION: _booking,
    DocumentType.COMMERCIAL_INVOICE: _invoice,
    DocumentType.BILL_OF_LADING: _bill_of_lading,
}


def analyze_core_document(
    path: Path,
    *,
    backend: OcrBackend | None = None,
) -> DocumentAnalysis:
    """Analyze one PDF without a database, workbook, vector store, or LLM."""
    layout = (backend or default_ocr_backend()).extract(path)
    classification = classify_trade_document(layout.text)
    source_sha256 = _sha256(path)
    if classification.doc_type is None:
        return DocumentAnalysis(
            file_name=path.name,
            source_sha256=source_sha256,
            status="UNSUPPORTED",
            doc_type=None,
            fields={},
            missing_core_fields=[],
            ocr_backend=layout.backend,
            ocr_fallback_used=layout.fallback_used,
            warnings=layout.warnings,
        )

    raw_fields = EXTRACTORS[classification.doc_type](layout)
    expected = CORE_FIELDS_BY_DOCUMENT[classification.doc_type]
    fields = {key: candidate.to_schema() for key, candidate in raw_fields.items()}
    missing = sorted(expected - set(fields))
    warnings = list(layout.warnings)
    if missing:
        warnings.append(f"Core fields require review: {', '.join(missing)}")
    status: Literal["ANALYZED", "REVIEW_REQUIRED"] = (
        "ANALYZED"
        if classification.status == "AUTO_CONFIRMED" and not missing
        else "REVIEW_REQUIRED"
    )
    return DocumentAnalysis(
        file_name=path.name,
        source_sha256=source_sha256,
        status=status,
        doc_type=classification.doc_type,
        fields=fields,
        missing_core_fields=missing,
        ocr_backend=layout.backend,
        ocr_fallback_used=layout.fallback_used,
        warnings=warnings,
    )


def analyze_core_documents(
    paths: Sequence[Path],
    *,
    backend: OcrBackend | None = None,
) -> list[DocumentAnalysis]:
    """Analyze a list of trade PDFs with one reusable OCR provider instance."""
    resolved_backend = backend or default_ocr_backend()
    return [analyze_core_document(path, backend=resolved_backend) for path in paths]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
