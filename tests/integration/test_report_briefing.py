from __future__ import annotations

from datetime import date
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import Base, TradeCase
from app.schemas.financial_calendar import (
    FinancialCalendarContract,
    FinancialCalendarEventRow,
    FinancialCalendarLinkRow,
)
from app.services.advisory import AdvisoryService
from app.services.financial_calendar import FinancialCalendarService
from app.services.financial_exposure import FinancialExposureService
from app.services.ingestion.batch_service import BatchService
from app.services.product_advisory import ProductAdvisoryService
from app.services.reports import ReportService

CUSTOMER_BLOCKS = [
    "거래 헤더",
    "선적 일정 변화",
    "지급기준일 변화",
    "금융 일정 신호 (우선순위 순)",
    "고객이 확인할 질문",
    "상담 선택지 / 다음 단계",
    "자료 기준·주의 문구",
    "근거·수정",
]
RM_BLOCKS = [
    "거래 요약",
    "충돌 우선순위 목록",
    "판정 근거 / 감사 정보",
    "고객 확인 이력",
    "라우팅 정보",
    "내부 질문 / 미확인 사항",
    "담당자·인계 상태",
    "근거·감사",
]


def _pdf_text(path: Path) -> str:
    """Extract normalized PDF text for audience-level regression assertions."""
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _transaction_paths() -> list[Path]:
    return sorted(
        (PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents").glob("*TRD-003*.pdf")
    )


def _txn003_financial_contract() -> FinancialCalendarContract:
    return FinancialCalendarContract(
        source_sha256="test-two-sheet-txn003",
        source_path="tests:two-sheet-txn003",
        version="financial-calendar.v2:test",
        company_id="A",
        events=[
            FinancialCalendarEventRow(
                event_id="FE001",
                event_type_code="WORKING_CAPITAL_LOAN_MATURITY",
                event_name="운전자금대출 만기",
                event_date=date(2026, 10, 3),
                amount=50_000,
                currency="USD",
                financial_institution="KB국민은행",
                is_kb_contract=True,
            )
        ],
        links=[
            FinancialCalendarLinkRow(
                link_id="LNK-TEST-TXN003-FE001",
                transaction_id="TXN003",
                event_id="FE001",
                link_type="EXPECTED_EXPORT_RECEIPT",
                link_status="CONFIRMED",
            )
        ],
    )


def test_reports_render_actual_workbook_blocks_from_one_risk_snapshot(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr("app.services.reports.REPORT_DIR", tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(_transaction_paths(), "REPORT-BRIEFING")
        batch.commit("REPORT-BRIEFING")
        FinancialCalendarService(session).import_contract(_txn003_financial_contract())
        AdvisoryService(session).record_shipment_delay_scenarios(
            "TRD-003",
            reported_at=date(2026, 7, 29),
            request_id="report-delay",
            expected_delay_days=[9],
        )
        exposure = FinancialExposureService(session).calculate_financial_exposure(
            "TRD-003",
            request_id="report-finance",
            confirmed_anchor="ON_BOARD_DATE",
        )

        product_service = ProductAdvisoryService()
        matched = product_service.match_scenario(
            query="수입대금과 운전자금 상담",
            scenario_codes=["SUPPLIER_PAYMENT"],
            customer_role="IMPORTER",
            borrower_type="CORPORATION",
        )
        selected = matched["products"][:2]
        evidence = product_service.retrieve_evidence(
            query="수입대금과 운전자금 상담",
            product_ids=[item["product_id"] for item in selected],
            matched_products=selected,
            top_k_per_product=1,
        )
        product_options = evidence["options"]

        reports = ReportService(session)
        payload = reports.build_briefing_payload(
            "TRD-003",
            product_options=product_options,
            as_of_date=date(2026, 7, 29),
            rm_inbox="rm@example.test",
        )
        assert [
            item["block"] for item in payload["blueprint"]["customer_sections"]
        ] == CUSTOMER_BLOCKS
        assert [item["block"] for item in payload["blueprint"]["rm_sections"]] == RM_BLOCKS
        assert payload["risk_snapshot"]["calculation_id"] == exposure.calculation_id
        assert payload["risk_snapshot"]["expected_receipt"]["date"] == "2026-10-08"
        assert all(option["citations"] for option in payload["product_options"])

        customer = reports.render_report(
            briefing_payload=payload,
            audience="CUSTOMER",
            consent=True,
        )
        rm_report = reports.render_report(
            briefing_payload=payload,
            audience="RM",
            consent=True,
        )
        assert customer["basis_version"] == rm_report["basis_version"]
        assert customer["payload_hash"] == rm_report["payload_hash"]
        for result in (customer, rm_report):
            report_path = Path(result["asset_path"])
            assert report_path.stat().st_size > 10_000
            assert len(PdfReader(report_path).pages) >= 1

        customer_text = _pdf_text(Path(customer["asset_path"]))
        rm_text = _pdf_text(Path(rm_report["asset_path"]))

        # 고객용은 DB·감사 구현 세부 대신 거래 상황과 실행 가능한 조치를 설명한다.
        for heading in (
            "현재 거래 요약",
            "문서 보유 현황",
            "핵심 일정",
            "권장 조치",
            "관련 KB 상품 후보",
        ):
            assert heading in customer_text
        for technical_label in (
            "payload_hash",
            "input_fingerprint",
            "source_sha256",
        ):
            assert technical_label not in customer_text

        # RM용은 고객 설명에 더해 식별·검증·상담 준비 근거를 포함한다.
        for heading in (
            "업무 식별정보",
            "문서 및 선적 증거",
            "지급조건 파싱 결과",
            "위험 계산 근거",
            "상담 준비 체크리스트",
            "고객 상담 질문 목록",
        ):
            assert heading in rm_text
        assert "TXN003" in rm_text
        assert "ON_BOARD_DATE" in rm_text


def test_no_snapshot_fallback_keeps_canonical_company_and_transaction_ids() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TradeCase(
                case_id="TRD-NO-SNAPSHOT",
                company_id=None,
                transaction_id="TXN-NO-SNAPSHOT",
                company="Fallback Company",
                counterparty="Buyer",
                invoice_no=None,
                currency="USD",
                goods="Components",
                status="AWAITING_DOCUMENT",
                monitoring_enabled=False,
                basis_version="basis-no-snapshot",
            )
        )
        session.flush()

        payload = ReportService(session).build_briefing_payload("TRD-NO-SNAPSHOT")
        assert payload["risk_snapshot"]["company_id"] is None
        assert payload["risk_snapshot"]["transaction_id"] == "TXN-NO-SNAPSHOT"
        assert payload["risk_snapshot"]["calculation_id"] is None
