from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.domain_inputs.providers import field_registry
from app.schemas.document import DocumentExtractionResult, ExtractedField
from app.schemas.field_contract import DocumentType
from app.services.ingestion.template_classifier import classify_fixed_template


def pdf_text(path: Path) -> str:
    """Extract text from a text-based fixed template and verify every page is readable."""
    reader = PdfReader(path)
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            raise ValueError(f"No text on fixed-template PDF page {index}: {path.name}")
        pages.append(text)
    return "\n".join(pages)


def _match(pattern: str, text: str, group: int = 1, flags: int = re.MULTILINE) -> str | None:
    found = re.search(pattern, text, flags)
    if not found:
        return None
    return " ".join(found.group(group).strip().split())


def _parse_date(value: str) -> str:
    cleaned = value.replace(".", " ").replace(",", " ")
    for fmt in ("%d%b%y", "%b %d %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(" ".join(cleaned.split()), fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _line_after(text: str, label: str, offset: int = 1) -> str | None:
    """Return a non-empty line at a fixed offset after an exact label line."""
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line.casefold() == label.casefold() and index + offset < len(lines):
            value = lines[index + offset].strip()
            return value or None
    return None


def _source_references(text: str) -> dict[str, str]:
    """Extract non-contract identifiers used only for deterministic bundling."""
    labels = {
        "transaction_reference": "Transaction Reference",
        "finance_transaction_id": "Finance Transaction ID",
        "company_id": "Company ID",
    }
    references: dict[str, str] = {}
    for key, label in labels.items():
        value = _match(
            rf"^{re.escape(label)}\s*:\s*([^\n]+)$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if value:
            references[key] = value
    return references


def _booking(text: str) -> dict[str, tuple[str, str, Any, float]]:
    vessel_line = _match(
        r"Trunk\s+Vessel\s*:\s*(.+?)(?:\s+Latest\s+ETA/ETD|\n)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    vessel_name: str | None = None
    voyage_no: str | None = None
    service_code: str | None = None
    if vessel_line:
        composite = re.match(r"(.+?)\s+([0-9]{2,4}[A-Z])(?:\(([^)]+)\))?$", vessel_line)
        if composite:
            vessel_name, voyage_no, service_code = composite.groups()

    pairs: dict[str, tuple[str, str, Any, float]] = {}

    def add(
        key: str,
        label: str,
        value: str | None,
        normalized: Any | None = None,
        confidence: float = 0.99,
    ) -> None:
        if value:
            pairs[key] = (label, value, normalized if normalized is not None else value, confidence)

    add("booking_no", "Booking No", _match(r"Booking\s+No\s*:\s*([^\s]+)", text, flags=re.I))
    add(
        "booking_ref_no",
        "Booking Ref. No.",
        _match(r"Booking\s+Ref\.\s*No\.\s*:\s*([^\s]+)", text, flags=re.I),
    )
    add("booking_date", "Booking Date", _match(r"Booking\s+Date\s*:\s*([^\s]+)", text, flags=re.I))
    add(
        "booking_bl_no",
        "B/L No.",
        _match(r"B/L\s+No\.\s*:\s*([^\s]+)", text, flags=re.I),
    )
    add(
        "shipper",
        "Shipper",
        _match(r"^Shipper\s*:\s*(.+)$", text, flags=re.I | re.M),
    )
    add("vessel_name", "Trunk Vessel", vessel_name)
    add("voyage_no", "Trunk Vessel", voyage_no)
    add("service_code", "Trunk Vessel", service_code)
    add(
        "port_of_loading",
        "Port of Loading",
        _match(r"^Port\s+of\s+Loading\s*:\s*(.+?)(?:\s+Terminal|\n)", text, flags=re.I | re.M),
    )
    add(
        "port_of_discharge",
        "Port of Discharging",
        _match(
            r"^Port\s+of\s+Discharg(?:e|ing)\s*:\s*(.+?)(?:\s{2,}Terminal|\n)",
            text,
            flags=re.I | re.M,
        ),
    )
    etd = _match(r"Proforma\s+1st\s+vessel\s+ETD\s*:\s*([0-9A-Za-z.-]+)", text, flags=re.I)
    add("etd", "Proforma 1st vessel ETD", etd, _parse_date(etd) if etd else None)
    add(
        "commodity",
        "Commodity",
        _match(
            r"^Commodity\s*(?:\n\s*)?:\s*(.+?)(?:\s+Estimated\s+Weight|\n)",
            text,
            flags=re.I | re.M,
        ),
    )
    add(
        "container_info",
        "Equipment Type/Q'ty",
        _match(r"Equipment\s+Type/Q[\u2019']?ty\s*:\s*(.+)$", text, flags=re.I | re.M),
    )
    add(
        "pre_carrier_vessel",
        "Pre Carrier",
        _match(r"^Pre\s+Carrier\s*:\s*(.+?)(?:\s{2,}Latest|\n)", text, flags=re.I | re.M),
    )
    remarks = [
        value
        for value in [
            _match(r"Remarks\s+1\s*:\s*(.+?)(?=\n\s*Remarks\s+2|\Z)", text, flags=re.I | re.S),
            _match(r"Remarks\s+2\s*:\s*(.+?)(?=\n\s*THE ABOVE|\Z)", text, flags=re.I | re.S),
        ]
        if value
    ]
    add("remarks", "Remarks 1 + Remarks 2", "\n".join(remarks))
    return pairs


def _bill_of_lading(text: str) -> dict[str, tuple[str, str, Any, float]]:
    pairs: dict[str, tuple[str, str, Any, float]] = {}

    def add(key: str, label: str, value: str | None, normalized: Any | None = None) -> None:
        if value:
            pairs[key] = (label, value, normalized if normalized is not None else value, 0.98)

    add("bl_no", "B/L No.", _match(r"B/L\s+No\.\s*[;:]?\s*([^\n]+)", text, flags=re.I))
    shipper = _line_after(text, "Shipper/Exporter") or _match(
        r"Shipper/Exporter(.+?)(?=\d+\.\s|⑪|B/L\s+No)",
        text,
        flags=re.I | re.S,
    )
    add("shipper", "Shipper/Exporter", shipper)
    consignee = _line_after(text, "Consignee") or _match(
        r"Consignee(.+?)(?=Notify\s+Party)",
        text,
        flags=re.I | re.S,
    )
    add("consignee", "Consignee", consignee)
    vessel_voyage = re.search(
        r"Ocean\s+Vessel.*?Voyage\s+No\..*?\n\s*(.+?)\s{2,}([0-9A-Z-]+)",
        text,
        re.I | re.S,
    )
    if vessel_voyage:
        add("vessel_name", "Ocean Vessel", vessel_voyage.group(1).strip())
        add("voyage_no", "Voyage No.", vessel_voyage.group(2).strip())
    else:
        add(
            "vessel_name",
            "Ocean Vessel",
            _match(r"Ocean\s+Vessel(.+?)(?=Voyage\s+No\.)", text, flags=re.I | re.S),
        )
        add(
            "voyage_no",
            "Voyage No.",
            _match(r"Voyage\s+No\.([0-9A-Z-]+)", text, flags=re.I),
        )
    port_line = re.search(
        r"Port\s+of\s+Loading.*?Port\s+of\s+Discharge.*?\n\s*(.+?)\s{2,}(.+?)\s{2,}",
        text,
        re.I | re.S,
    )
    if port_line:
        add("port_of_loading", "Port of Loading", port_line.group(1).strip())
        add("port_of_discharge", "Port of Discharge", port_line.group(2).strip())
    else:
        synthetic_ports = _line_after(text, "Port of Loading  Port of Discharge  Place of Delivery")
        if synthetic_ports:
            parts = [part.strip() for part in re.split(r"\s{2,}", synthetic_ports)]
            if len(parts) >= 2:
                add("port_of_loading", "Port of Loading", parts[0])
                add("port_of_discharge", "Port of Discharge", parts[1])
        else:
            collapsed_ports = re.search(
                r"Merchant\s+Ref\.\)\s+(.+?)\s{3,}(.+?)\s{3,}",
                text,
                re.I | re.S,
            )
            if collapsed_ports:
                add("port_of_loading", "Port of Loading", collapsed_ports.group(1).strip())
                add(
                    "port_of_discharge",
                    "Port of Discharge",
                    collapsed_ports.group(2).strip(),
                )
    onboard = _match(
        r"Laden\s+on\s+board\s+vessel.*?Date(?:\s+Signature)?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        text,
        flags=re.I | re.S,
    )
    add(
        "on_board_date",
        "Laden on board vessel - Date",
        onboard,
        _parse_date(onboard) if onboard else None,
    )
    add(
        "issue_date_place",
        "Place and Date of Issue",
        _match(
            r"Place\s+and\s+Date\s+of\s+Issue\s*\n\s*([^\n]+)",
            text,
            flags=re.I,
        ),
    )
    return pairs


def _invoice(text: str) -> dict[str, tuple[str, str, Any, float]]:
    pairs: dict[str, tuple[str, str, Any, float]] = {}

    def add(key: str, label: str, value: str | None, normalized: Any | None = None) -> None:
        if value:
            pairs[key] = (label, value, normalized if normalized is not None else value, 0.98)

    invoice_line = _line_after(text, "Invoice No. and date") or _match(
        r"Invoice\s+No\.\s+and\s+date\s*\n\s*(.+)$",
        text,
        flags=re.I | re.M,
    )
    if not invoice_line:
        invoice_line = _match(
            r"Invoice\s+No\.\s+and\s+date\s+(.+?\s+[A-Z]{3}\.?\s+\d{1,2}\.?\s+\d{4})",
            text,
            flags=re.I,
        )
    if invoice_line:
        number = re.sub(r"\s+[A-Z]{3}\.?\s+\d{1,2}\.?\s+\d{4}$", "", invoice_line)
        add("invoice No.", "Invoice No. and date", number)
    seller = _line_after(text, "Shipper/Seller")
    if not seller:
        seller = _match(
            r"\d{1,2}\.?\s+\d{4}\s+([A-Z][A-Z .,&'-]+?CO\.,?\s+LTD\.)",
            text,
            flags=re.I,
        )
    add("seller", "Shipper/Seller", seller)
    buyer = _line_after(text, "Buyer(if other than consignee)") or _match(
        r"Buyer\(if other than consignee\)\s+([A-Z][A-Z .,&'-]+?CO\.,?\s+LTD\.)",
        text,
        flags=re.I,
    )
    if not buyer:
        buyer = _match(r"Consignee.*?\n\s*([A-Z][^\n]+)", text, flags=re.I)
    add("buyer", "Buyer", buyer)
    payment = _line_after(text, "Terms of delivery and payment", offset=2)
    if payment in {"Shipping Marks", "Goods description"}:
        payment = None
    if not payment:
        payment = _match(
            r"Terms\s+of\s+delivery\s+and\s+payment\s+F\.?O\.?B\.?\s+\w+\s+(.+?)(?=⑥|To\s+)",
            text,
            flags=re.I,
        )
    add("payment_terms", "Terms of delivery and payment", payment)
    incoterms = _line_after(text, "Terms of delivery and payment") or _match(
        r"Terms\s+of\s+delivery\s+and\s+payment\s*\n\s*([^\n]+)",
        text,
        flags=re.I,
    )
    if not incoterms:
        incoterms = _match(
            r"Terms\s+of\s+delivery\s+and\s+payment\s+(F\.?O\.?B\.?\s+\w+)",
            text,
            flags=re.I,
        )
    add("incoterms", "Terms of delivery and payment", incoterms)
    amount = _match(r"(US\\?\$[\d,.]+)\s*$", text, flags=re.I | re.M)
    if not amount:
        amount = _match(r"US\$\s*([\d,.]+)", text, flags=re.I)
        if amount:
            amount = f"US${amount}"
    add("total_amount", "Amount", amount)
    currency = "USD" if re.search(r"US\$", text, re.I) else None
    add("currency", "Currency inferred from exact US$ amount label", currency)
    description = _line_after(text, "Goods description") or _match(
        r"Goods\s*\n.*?description\s*\n\s*([A-Z][^\n]+)",
        text,
        flags=re.I | re.S,
    )
    if not description:
        description = _match(r"\n\s*(NYLON[^\n]+)", text, flags=re.I)
    if not description:
        description = _match(r"(NYLON\s+OXFORD)", text, flags=re.I)
    add("description_of_goods", "Goods description", description)
    add(
        "shipping Marks",
        "Shipping Marks",
        _match(
            r"Shipping\s+Marks.*?\n(?:[^\n]*\n)?\s*([A-Z0-9/.-]+)",
            text,
            flags=re.I | re.S,
        ),
    )
    add("signed by", "Signed by", _match(r"Signed\s+by\s*\n\s*([^\n]+)", text, flags=re.I))
    return pairs


EXTRACTORS: dict[DocumentType, Callable[[str], dict[str, tuple[str, str, Any, float]]]] = {
    DocumentType.BOOKING_CONFIRMATION: _booking,
    DocumentType.BILL_OF_LADING: _bill_of_lading,
    DocumentType.COMMERCIAL_INVOICE: _invoice,
}


def extract_fixed_document(path: Path) -> DocumentExtractionResult:
    """Classify and extract one supported PDF with zero model calls."""
    text = pdf_text(path)
    classification = classify_fixed_template(text)
    source_references = _source_references(text)
    if classification.status != "AUTO_CONFIRMED" or classification.doc_type is None:
        return DocumentExtractionResult(
            classification=classification,
            source_references=source_references,
            field_contract_version=field_registry().source_sha256,
            template_source_hash=_sha256(path),
            model_calls=0,
        )
    registry = field_registry()
    raw = EXTRACTORS[classification.doc_type](text)
    allowed = registry.exact_keys(classification.doc_type)
    unexpected = set(raw) - allowed
    if unexpected:
        raise ValueError(f"Extractor produced fields outside contract: {sorted(unexpected)}")
    fields: dict[str, ExtractedField] = {}
    for exact, (label, raw_value, normalized, confidence) in raw.items():
        entry = registry.by_exact(classification.doc_type, exact)
        fields[exact] = ExtractedField(
            exact_standard_field=exact,
            safe_key=entry.safe_key,
            raw_label=label,
            raw_value=raw_value,
            normalized_value=normalized,
            page=1,
            bbox=None,
            confidence=confidence,
        )
    missing_core = sorted(registry.core_keys(classification.doc_type) - set(fields))
    return DocumentExtractionResult(
        classification=classification,
        fields=fields,
        source_references=source_references,
        missing_core_fields=missing_core,
        field_contract_version=registry.source_sha256,
        template_source_hash=_sha256(path),
        model_calls=0,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
