from __future__ import annotations

from datetime import date
from pathlib import Path

from pypdf import PdfReader

from app.schemas.portfolio import PortfolioReportPayload
from app.services.report_generator import PortfolioReportGenerator


def test_report_generator_renders_only_frozen_agent_results(tmp_path: Path) -> None:
    payload = PortfolioReportPayload.model_validate(
        {
            "generated_on": "2026-08-28",
            "expected_receipt_date": "2026-09-20",
            "documents": [
                {
                    "file_name": "invoice.pdf",
                    "document_type": "COMMERCIAL_INVOICE",
                    "source_sha256": "a" * 64,
                    "fields": {"total_amount": "USD 50,000", "currency": "USD"},
                    "evidence": [
                        {"field": "total_amount", "page": 1, "bbox": [0.1, 0.2, 0.3, 0.4]}
                    ],
                }
            ],
            "conflicts": [
                {
                    "event_id": "EV-1",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "status": "CONFLICT",
                    "event_name": "공급자 지급",
                    "event_date": "2026-09-15",
                    "expected_receipt_date": "2026-09-20",
                    "gap_days": 5,
                    "reason": "공급자 지급일이 예상 대금 유입일보다 5일 빠릅니다.",
                }
            ],
            "product_options": [
                {
                    "product_id": "payment-usance",
                    "product_name": "KB Payment Usance",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "why_consider": "수입대금 결제 시점의 자금 공백을 검토할 수 있습니다.",
                    "citations": [
                        {
                            "source_file": "payment-usance.pdf",
                            "page": 2,
                            "source_sha256": "b" * 64,
                            "excerpt": "수입 결제 관련 상품 안내",
                            "chunk_id": "chunk-1",
                        }
                    ],
                }
            ],
        }
    )
    target = tmp_path / "analysis.pdf"

    result = PortfolioReportGenerator().generate(payload, target)

    assert result["report_path"] == str(target)
    assert result["conflict_count"] == 1
    assert result["product_option_count"] == 1
    assert result["page_count"] == 2
    assert len(result["sha256"]) == 64
    assert target.stat().st_size > 5_000
    reader = PdfReader(target)
    assert len(reader.pages) == 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "금융충돌 분류" in text
    assert "KB Payment Usance" in text
    assert "payment-usance.pdf" in text


def test_report_generator_abstains_without_product_evidence(tmp_path: Path) -> None:
    payload = PortfolioReportPayload(
        generated_on=date(2026, 8, 28),
        documents=[],
        conflicts=[],
        product_options=[],
        warnings=["입력 확인이 필요하여 상품 검색을 생략했습니다."],
    )
    target = tmp_path / "no-evidence.pdf"

    PortfolioReportGenerator().generate(payload, target)

    text = "\n".join(page.extract_text() or "" for page in PdfReader(target).pages)
    assert "제공할 상품 근거가 없습니다" in text
    assert "입력 확인이 필요하여 상품 검색을 생략했습니다" in text
