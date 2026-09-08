from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader

from app.agents.document_agent import DocumentAgent
from app.agents.finance_advisor import FinanceAdvisorAgent
from app.graphs.compiled import build_portfolio_graph
from app.schemas.portfolio import PortfolioDocumentResult
from app.services.financial_retrieval import FinancialRetrieval
from app.services.portfolio_pipeline import PortfolioPipeline
from tests.helpers import FULL_PLAN, ScriptedModel
from tests.unit.test_finance_advisor import FakeStore, catalog, request, submission


class StubDocumentAgent:
    def run(self, paths: list[Path], **kwargs: Any) -> list[PortfolioDocumentResult]:
        return [
            PortfolioDocumentResult(
                file_name=paths[0].name,
                document_type="COMMERCIAL_INVOICE",
                source_sha256="a" * 64,
                fields={"currency": "USD"},
                ocr_backend="fake",
            )
        ]


def test_supervised_graph_returns_cited_product_and_pdf(tmp_path: Path) -> None:
    fake = ScriptedModel(
        [
            FULL_PLAN,
            "call_document_agent",
            "call_finance_agent",
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            "generate_report",
        ]
    )
    pipeline = PortfolioPipeline(
        model=fake,
        document_agent=cast(DocumentAgent, StubDocumentAgent()),
        finance_agent=FinanceAdvisorAgent(
            retrieval=FinancialRetrieval(store=FakeStore(), catalog=catalog())
        ),
    )
    req = request(
        document_paths=[tmp_path / "invoice.pdf"], report_output_path=tmp_path / "report.pdf"
    )
    result = pipeline.result(build_portfolio_graph(pipeline).invoke({"request": req.model_dump()}))
    assert result.status == "SUCCESS"
    assert result.trace[0] == "prepare" and result.trace[-1] == "finalize"
    assert result.llm_calls == 7
    assert all(a.status == "SUCCESS" for a in result.tool_audit)
    assert result.product_options[0].citations[0].page == 2
    assert result.report_path and Path(result.report_path).is_file()
    text = "\n".join(p.extract_text() for p in PdfReader(result.report_path).pages)
    assert "USANCE.pdf" in text and "추가확인질문" in "".join(text.split())
    assert result.service_cards[0].service_code == "SUPPLIER_FUNDING_REVIEW"


def test_all_three_agents_with_real_chroma_and_mock_llm(tmp_path: Path) -> None:
    from app.services.ingestion.ocr_backend import NativePdfLayoutBackend
    from app.services.product_vector_store import ChromaProductVectorStore, ProductChunk
    from tests.integration.test_chroma_integration import TinyEmbedding

    store = ChromaProductVectorStore(
        persist_directory=tmp_path / "chroma",
        embedding_provider=TinyEmbedding(),
        validate_manifest=False,
    )
    store.replace(
        [
            ProductChunk(
                chunk_id="test-c1",
                document="TEST: 수입거래용 자료. 대상 조건은 은행 확인 필요.",
                metadata={
                    "product_id": "USANCE",
                    "product_name": "USANCE",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "source_file": "USANCE.pdf",
                    "page": 2,
                    "source_sha256": "a" * 64,
                    "chunk_index": 0,
                    "topic": "FUNDING_PURPOSE",
                    "section_id": "test-purpose",
                },
            )
        ]
    )
    fake = ScriptedModel(
        [
            FULL_PLAN,
            "call_document_agent",
            "read_pdf",
            (
                "validate_fields",
                {
                    "documents": [
                        {
                            "document_id": "doc-1",
                            "document_type": "BOOKING_CONFIRMATION",
                            "fields": [
                                {"field": "booking_no", "raw_value": "BK-DEMO-001", "page": 1},
                                {"field": "etd", "raw_value": "2026-09-18", "page": 1},
                            ],
                        }
                    ]
                },
            ),
            "call_finance_agent",
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            "generate_report",
        ]
    )
    pipeline = PortfolioPipeline(
        model=fake,
        document_agent=DocumentAgent(backend=NativePdfLayoutBackend()),
        finance_agent=FinanceAdvisorAgent(
            retrieval=FinancialRetrieval(store=store, catalog=catalog())
        ),
    )
    fixture = Path(__file__).resolve().parents[2] / "data/fixtures/tradeflow_example/01_booking.pdf"
    req = request(document_paths=[fixture], report_output_path=tmp_path / "three-agents.pdf")
    result = pipeline.result(build_portfolio_graph(pipeline).invoke({"request": req.model_dump()}))
    assert result.status == "REVIEW_REQUIRED"  # native text geometry notice is retained
    assert result.documents[0].fields["etd"] == "2026-09-18"
    assert result.conflicts[0].status == "CONFLICT"
    assert result.product_options[0].citations[0].chunk_id == "test-c1"
    assert result.llm_calls == 9 and not fake.actions
    assert result.report_path and Path(result.report_path).is_file()
    assert "2026-09-18" in "".join(p.extract_text() for p in PdfReader(result.report_path).pages)
