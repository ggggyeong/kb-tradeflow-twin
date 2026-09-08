"""Only explicit calendar-day payment terms with verified source documents are calculated."""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.schemas.portfolio import PortfolioDocumentResult, PortfolioRunRequest, ReceiptResolution

IDENTITY_FIELDS = {
    "COMMERCIAL_INVOICE": "invoice_no",
    "BILL_OF_LADING": "bl_no",
    "BOOKING_CONFIRMATION": "booking_no",
}


def resolve_receipt_date(
    request: PortfolioRunRequest,
    documents: list[PortfolioDocumentResult],
    references: dict[str, str],
) -> ReceiptResolution:
    def review(reason: str) -> ReceiptResolution:
        return ReceiptResolution(status="REVIEW_REQUIRED", basis=reason)

    verified = bool(references and documents)
    by_type: dict[str, PortfolioDocumentResult] = {}
    for document in documents:
        field = IDENTITY_FIELDS.get(document.document_type)
        if not field or document.document_type in by_type:
            return review(
                "문서 종류 미확인 또는 같은 종류의 중복 문서가 있어 거래 연결 확인이 필요합니다."
            )
        by_type[document.document_type] = document
        if not references.get(field):
            verified = False
        elif document.fields.get(field) != references[field]:
            return review("엑셀의 거래 문서 번호와 업로드 서류가 일치하지 않습니다.")
    if request.expected_receipt_date:
        # An explicitly supplied date is usable even for the legacy two-sheet workbook.
        return ReceiptResolution(
            status="USER_PROVIDED",
            expected_receipt_date=request.expected_receipt_date,
            basis="사용자가 확인해 입력한 예상 대금 유입일입니다.",
            document_links_verified=verified,
        )
    if not verified:
        return review("자동 계산에는 엑셀 거래정보와 서류 번호의 일치 확인이 필요합니다.")
    if request.trade_direction != "EXPORT":
        return review("수출대금 유입일 자동 계산은 수출거래만 지원합니다.")
    invoice = by_type.get("COMMERCIAL_INVOICE")
    terms = str(invoice.fields.get("payment_terms", "")) if invoice else ""
    match = re.fullmatch(
        r"(?:T/T\s+)?(\d{1,3})\s+(?:CALENDAR\s+)?DAYS\s+AFTER\s+(B/L\s+ON\s+BOARD\s+DATE|INVOICE\s+DATE)",
        terms.strip(),
        re.I,
    )
    if not match:
        return review(
            "결제조건 또는 기준일이 불명확합니다. B/L DATE만 적힌 조건·L/C AT SIGHT·분할 지급·영업일 조건은 담당자 확인이 필요합니다."
        )
    days = int(match[1])
    if not 1 <= days <= 365:
        return review("결제조건의 일수는 1~365일 범위에서 확인해 주세요.")
    on_board = match[2].upper().startswith("B/L")
    anchor_doc = by_type.get("BILL_OF_LADING") if on_board else invoice
    anchor_field = "on_board_date" if on_board else "invoice_date"
    if not anchor_doc or not anchor_doc.fields.get(anchor_field):
        return review("결제조건의 기준일이 없습니다. Booking 예정 출항일로 대신 계산하지 않습니다.")
    anchor = date.fromisoformat(str(anchor_doc.fields[anchor_field]))
    relevant = [(invoice, "payment_terms"), (anchor_doc, anchor_field)]
    evidence = [
        {"source_file": doc.file_name, "source_sha256": doc.source_sha256, **item}
        for doc, field in relevant
        if doc
        for item in doc.evidence
        if item.get("field") == field
    ]
    # Format validation alone does not validate a source span or weak OCR.
    if len(evidence) != 2 or any(float(item.get("confidence", 0)) < 0.8 for item in evidence):
        return review("결제조건 또는 기준일의 원문·OCR 근거 확인이 필요합니다.")
    if any(item.get("ambiguous") for item in evidence):
        return review("결제조건 또는 기준일의 원문이 중복되어 담당자 확인이 필요합니다.")
    term_evidence = next(item for item in evidence if item["field"] == "payment_terms")
    source_line = str(term_evidence.get("source_line", ""))
    if source_line:
        complete_terms = re.sub(r"^.*?Payment\s+Terms\s*:\s*", "", source_line, flags=re.I).strip()
        if complete_terms != terms:
            return review(
                "결제조건 원문의 일부만 추출되었습니다. 분할 지급 등 전체 조건을 확인해 주세요."
            )
    return ReceiptResolution(
        status="CALCULATED",
        expected_receipt_date=anchor + timedelta(days=days),
        document_links_verified=True,
        evidence=evidence,
        basis=f"{anchor_field} {anchor.isoformat()} + {days} calendar days ({terms}). 계약상 예정일이며 휴일·은행 처리·입금 지연은 반영하지 않습니다.",
    )
