"""Real XLSX/PDF/OCR/E5/Chroma/report checks. LLM is explicitly scripted, not live."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from app.agents.document_agent import DocumentAgent
from app.agents.finance_advisor import FinanceAdvisorAgent
from app.graphs.compiled import build_portfolio_graph
from app.schemas.orchestration import ModelCall
from app.services.llm_controller import RunModel
from app.services.portfolio_pipeline import PortfolioPipeline
from scripts.run_example import load_example
from tests.helpers import FULL_PLAN, ScriptedModel
from tests.unit.test_receipt_date import extraction_submission

ROOT = Path(__file__).resolve().parents[2]
local_models = pytest.mark.skipif(
    os.getenv("TRADEFLOW_LOCAL_MODELS") != "1",
    reason="Set TRADEFLOW_LOCAL_MODELS=1 after downloading local OCR/E5 and building the index; no OpenAI calls.",
)


class GroundedScriptedModel(ScriptedModel):
    """Only the final fixture reply is assembled from actual search IDs, never made up."""

    def call(self, **kwargs: Any) -> ModelCall:
        if kwargs.get("forced_tool") == "choose_financial_evidence":
            observation = json.loads(kwargs["messages"][-1]["content"])
            reply = (
                "choose_financial_evidence",
                {
                    "selections": {
                        question["slot_id"]: question["evidence_ids"][0]
                        if question["evidence_ids"]
                        else None
                        for question in observation["questions"]
                    },
                },
            )
            self.actions.insert(0, reply)
        elif kwargs.get("forced_tool") == "explain_financial_evidence":
            observation = json.loads(kwargs["messages"][-1]["content"])
            assert set(observation) == {"question", "source_text"}
            self.actions.insert(
                0,
                (
                    "explain_financial_evidence",
                    {"text": "모의 검증 설명: 실제 검색 본문에서 해당 조건을 확인해야 합니다."},
                ),
            )
        return super().call(**kwargs)


@local_models
def test_scanned_invoice_is_read_by_actual_ocr() -> None:
    request = load_example()
    assert not (PdfReader(request.document_paths[1]).pages[0].extract_text() or "").strip()
    fake = ScriptedModel(["read_pdf", extraction_submission()])
    documents = DocumentAgent().run(request.document_paths, model=RunModel(fake, 12))
    invoice = documents[1]
    assert invoice.ocr_backend == "rapidocr_onnx"
    assert invoice.fields["invoice_no"] == "INV-DEMO-001"
    assert invoice.fields["total_amount"] == "50000.00"
    assert invoice.fields["payment_terms"] == "T/T 30 CALENDAR DAYS AFTER B/L ON BOARD DATE"
    assert not invoice.warnings


@local_models
def test_real_local_pipeline_uses_excel_ocr_e5_chroma_and_report() -> None:
    request = load_example()
    output = ROOT / "output/pdf/tradeflow-example-local-verified.pdf"
    request = request.model_copy(update={"report_output_path": output})
    fake = GroundedScriptedModel(
        [
            FULL_PLAN,
            "call_document_agent",
            "read_pdf",
            extraction_submission(),
            "call_finance_agent",
            "read_financial_calendar",
            "compare_financial_dates",
            "search_financial_documents",
            "generate_report",
        ]
    )
    pipeline = PortfolioPipeline(model=fake, finance_agent=FinanceAdvisorAgent())
    result = pipeline.result(
        build_portfolio_graph(pipeline).invoke({"request": request.model_dump()})
    )
    assert str(result.expected_receipt_date) == "2026-10-21", result.warnings
    assert {c.event_id: c.gap_days for c in result.conflicts} == {
        "DEMO-LOAN": 6,
        "DEMO-FX": 5,
        "DEMO-PAY": 2,
    }
    assert all(c.status == "CONFLICT" for c in result.conflicts)
    assert {o.event_id for o in result.product_options} == {"DEMO-LOAN", "DEMO-FX", "DEMO-PAY"}
    assert result.receipt_resolution and result.receipt_resolution.document_links_verified
    assert any(d.ocr_backend == "rapidocr_onnx" for d in result.documents)
    assert result.status != "FAILED" and result.llm_calls == 20
    assert not fake.actions
    for option in result.product_options:
        assert option.financial_institution
        for citation in option.citations:
            page = PdfReader(ROOT / "data/knowledge/products" / citation.source_file).pages[
                citation.page - 1
            ]
            assert citation.excerpt in " ".join((page.extract_text() or "").split())
    assert output.is_file() and len(PdfReader(output).pages) >= 2
    payload = result.model_dump(mode="json")
    payload["verification"] = {
        "live_llm_executed": False,
        "llm_mode": "scripted_fixture",
        "real_components": [
            "XLSX",
            "PDF",
            "RapidOCR",
            "multilingual-e5-small",
            "ChromaDB",
            "reportlab",
        ],
    }
    target = ROOT / "output/example/local-result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
