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


def test_report_renders_generated_explanation_with_its_quote_and_human_review_notice(
    tmp_path: Path,
) -> None:
    from app.schemas.portfolio import PortfolioConflictResult, PortfolioProductOption
    from app.services.service_policy import build_service_cards

    citation = {
        "source_file": "test-bank-guide.pdf",
        "page": 4,
        "source_sha256": "b" * 64,
        "excerpt": "이 테스트 자료의 대출 연장은 은행 심사에 따라 거절될 수 있습니다.",
        "chunk_id": "test-chunk-4",
        "citation_id": "ref-1",
    }
    option = PortfolioProductOption.model_validate(
        {
            "product_id": "TEST-GUIDE",
            "product_name": "TEST BANK GUIDE",
            "event_id": "LOAN-1",
            "scenario_code": "WORKING_CAPITAL_LOAN_MATURITY",
            "why_consider": "상환 전 연장 조건을 확인하는 참고 자료입니다.",
            "explanation_points": [
                {
                    "text": "이 자료에서 만기 연장은 자동으로 보장되지 않으며 은행 심사가 필요합니다.",
                    "supporting_quote": citation["excerpt"],
                    "citations": [citation],
                }
            ],
            "citations": [citation],
        }
    )
    conflict = PortfolioConflictResult(
        event_id="LOAN-1",
        scenario_code="WORKING_CAPITAL_LOAN_MATURITY",
        status="CONFLICT",
        event_name="대출 만기",
        reason="만기가 입금 예정일보다 6일 빠릅니다.",
    )
    payload = PortfolioReportPayload(
        generated_on=date(2026, 9, 8),
        documents=[],
        conflicts=[conflict],
        product_options=[option],
        service_cards=build_service_cards([conflict], [option]),
    )
    target = tmp_path / "grounded-explanation.pdf"
    PortfolioReportGenerator().generate(payload, target)
    text = " ".join(page.extract_text() or "" for page in PdfReader(target).pages)
    compact = "".join(text.split())
    for expected in (
        "핵심 설명",
        "자동으로 보장되지",
        "근거 원문",
        "test-bank-guide.pdf p.4",
        "담당자의 검토가 필요합니다",
    ):
        assert "".join(expected.split()) in compact
