from datetime import date
from pathlib import Path
from typing import cast

from pypdf import PdfReader

from app.agents.document_agent import DocumentAgent
from app.agents.product_advisor_agent import ProductAdvisorAgent
from app.graphs.compiled import build_portfolio_graph
from app.schemas.portfolio import (
    PortfolioDocumentResult,
    PortfolioProductOption,
    PortfolioRunRequest,
)
from app.services.portfolio_pipeline import PortfolioPipeline


class StubDocumentAgent:
    def run(self, paths: list[Path]) -> list[PortfolioDocumentResult]:
        return [
            PortfolioDocumentResult(
                file_name=paths[0].name,
                document_type="COMMERCIAL_INVOICE",
                source_sha256="a" * 64,
                status="ANALYZED",
                ocr_backend="fake_ocr",
                fields={"total_amount": "50000", "currency": "USD"},
            )
        ]


class StubProductAdvisorAgent:
    def run(self, conflicts: list[object]) -> list[PortfolioProductOption]:
        assert conflicts
        return [
            PortfolioProductOption.model_validate(
                {
                    "product_id": "KB-PAYMENT-USANCE",
                    "product_name": "KB Payment Usance",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "why_consider": "결제 자금 공백 대응 수단으로 검토합니다.",
                    "citations": [
                        {
                            "source_file": "payment-usance.pdf",
                            "page": 1,
                            "source_sha256": "b" * 64,
                            "excerpt": "수입대금을 송금하고 만기에 결제하는 상품",
                            "chunk_id": "chunk-1",
                        }
                    ],
                }
            )
        ]


def test_four_step_graph_returns_cited_product_and_pdf(tmp_path: Path) -> None:
    pipeline = PortfolioPipeline(
        document_agent=cast(DocumentAgent, StubDocumentAgent()),
        product_advisor_agent=cast(ProductAdvisorAgent, StubProductAdvisorAgent()),
        today=lambda: date(2026, 8, 28),
    )
    graph = build_portfolio_graph(pipeline)
    request = PortfolioRunRequest.model_validate(
        {
            "document_paths": [tmp_path / "invoice.pdf"],
            "expected_receipt_date": "2026-09-20",
            "financial_events": [
                {
                    "event_id": "PAY-1",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "event_name": "공급자 지급",
                    "event_date": "2026-09-15",
                    "amount": 50_000,
                    "currency": "USD",
                }
            ],
            "report_output_path": tmp_path / "portfolio-report.pdf",
        }
    )

    state = graph.invoke({"request": request.model_dump(mode="python")})
    result = pipeline.result(state)

    assert result.status == "SUCCESS"
    assert result.trace == [
        "document_agent",
        "financial_conflict_agent",
        "product_advisor_agent",
        "report_generator",
    ]
    assert result.conflicts[0].status == "CONFLICT"
    assert result.product_options[0].citations[0].page == 1
    assert Path(result.report_path).is_file()
    assert len(PdfReader(result.report_path).pages) == 2
