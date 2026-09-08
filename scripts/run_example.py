"""One explicitly authorized low-cost live run; never silently substitutes a fake LLM."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paths import PRODUCT_CATALOG_PATH, PRODUCT_PDF_DIR, PRODUCT_VECTOR_DIR  # noqa: E402
from app.graphs.compiled import build_portfolio_graph  # noqa: E402
from app.schemas.orchestration import ModelCall  # noqa: E402
from app.schemas.portfolio import PortfolioRunRequest  # noqa: E402
from app.services.financial_calendar import read_financial_calendar  # noqa: E402
from app.services.llm_controller import LLMError, LLMSettings, OpenAIToolCallingModel  # noqa: E402
from app.services.portfolio_pipeline import PortfolioPipeline  # noqa: E402
from app.services.product_vector_store import ChromaProductVectorStore  # noqa: E402
from app.services.service_policy import get_answer_slots  # noqa: E402


class RecordedLiveModel:
    """Opt-in synthetic-example audit: arguments/usage only, never keys or reasoning."""

    def __init__(self, settings: LLMSettings, path: Path) -> None:
        self.delegate = OpenAIToolCallingModel(settings)
        self.path = path
        self.records: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.attempts = 0

    def call(self, **kwargs: Any) -> ModelCall:
        self.attempts += 1
        try:
            result = self.delegate.call(**kwargs)
        except LLMError as exc:
            # LLMError contains only our sanitized message, never provider bodies.
            self.failures.append(
                {
                    "attempt": self.attempts,
                    "tool": kwargs.get("forced_tool"),
                    "reason": str(exc),
                }
            )
            self.path.with_name(self.path.stem + "-errors.json").write_text(
                json.dumps(self.failures, ensure_ascii=False, indent=2)
            )
            print(f"LLM attempt {self.attempts}: {exc}", flush=True)
            raise
        self.records.append(result.model_dump(exclude={"response_items"}))
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2))
        print(f"LLM attempt {self.attempts}: {result.name} completed", flush=True)
        return result


def load_example() -> PortfolioRunRequest:
    raw = json.loads((ROOT / "data/fixtures/tradeflow_example/request.json").read_text())
    raw["document_paths"] = [ROOT / path for path in raw["document_paths"]]
    for key in ("financial_calendar_path", "report_output_path"):
        raw[key] = ROOT / raw[key]
    return PortfolioRunRequest.model_validate(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Actually call OpenAI, at most 20 calls; paid."
    )
    args = parser.parse_args()
    settings = LLMSettings.from_env().model_copy(
        update={
            "model": "gpt-5-nano",
            "service_tier": "flex",
            "max_calls": 20,
            "max_output_tokens": 6000,
            "timeout_seconds": 180,
        }
    )
    request = load_example()
    assert request.financial_calendar_path and request.transaction_id
    read_financial_calendar(request.financial_calendar_path, request.transaction_id)
    for path in request.document_paths:
        if not path.is_file():
            raise ValueError("Example document is missing; run build_example_documents.py")
    store = ChromaProductVectorStore(persist_directory=PRODUCT_VECTOR_DIR)
    manifest = store.validate_manifest(
        pdf_directory=PRODUCT_PDF_DIR, catalog_path=PRODUCT_CATALOG_PATH
    )
    key_present = bool(os.getenv("OPENAI_API_KEY", "").strip())
    status = {
        "checked_at": datetime.now(UTC).isoformat(),
        "mode": "preflight",
        "live_llm_executed": False,
        "api_key_configured": key_present,
        "model": settings.model,
        "service_tier": settings.service_tier,
        "max_calls": settings.max_calls,
        "indexed_records": manifest["record_count"],
    }
    output = ROOT / "output" / "example"
    output.mkdir(parents=True, exist_ok=True)
    if not args.live or not key_present:
        status["next_step"] = (
            "Run with --live to call OpenAI"
            if key_present
            else "Configure a NEW OPENAI_API_KEY in local .env; never paste a key in chat."
        )
        (output / "preflight.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 2 if args.live else 0
    history = output / "live-runs"
    history.mkdir(exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    model = RecordedLiveModel(settings, history / f"{run_id}-calls.json")
    pipeline = PortfolioPipeline(settings=settings, model=model)
    print("Starting one gpt-5-nano Flex run. No automatic retries or tier upgrades.", flush=True)
    result = pipeline.result(
        build_portfolio_graph(pipeline).invoke({"request": request.model_dump()})
    )
    payload = result.model_dump(mode="json")
    payload["verification"] = {
        "live_llm_executed": True,
        "model": settings.model,
        "requested_service_tier": settings.service_tier,
        "served_service_tiers": sorted(
            {r["served_service_tier"] or "unknown" for r in model.records}
        ),
        "run_id": run_id,
        "completed_llm_calls": len(model.records),
        "failed_llm_calls": model.failures,
        "index": {
            key: manifest[key]
            for key in (
                "schema_version",
                "collection_name",
                "product_count",
                "record_count",
                "unique_chunk_count",
                "corpus_fingerprint",
            )
        },
        "estimated_usd_upper_standard_rate": round(
            (result.input_tokens * 0.05 + result.output_tokens * 0.40) / 1_000_000, 8
        ),
        "cost_note": "2026-09-06 official standard token rates, ignoring cache discounts; not an invoice. Incomplete/error responses may have unreported usage. Flex is lower; no automatic standard fallback.",
    }
    expected = json.loads((ROOT / "data/fixtures/tradeflow_example/expected.json").read_text())
    checks = {
        "all_documents_analyzed": len(result.documents) == 3
        and all(d.status == "ANALYZED" for d in result.documents),
        "receipt_date": str(result.expected_receipt_date) == expected["expected_receipt_date"],
        "three_conflicts": {
            c.event_id: c.gap_days for c in result.conflicts if c.status == "CONFLICT"
        }
        == {c["event_id"]: c["gap_days"] for c in expected["expected_conflicts"]},
        "all_cases_have_evidence": {o.event_id for o in result.product_options}
        == {c["event_id"] for c in expected["expected_conflicts"]},
        "three_service_cards": len(result.service_cards) == 3
        and all(c.status == "INFORMATION" for c in result.service_cards),
        "quotes_match_sources": bool(result.product_options)
        and all(
            p.supporting_quote in c.excerpt
            for o in result.product_options
            for p in o.explanation_points
            for c in p.citations
        ),
        "page_provenance_present": bool(result.product_options)
        and all(
            c.page >= 1 and c.chunk_id and c.source_sha256
            for o in result.product_options
            for c in o.citations
        ),
        "generated_explanations_present": bool(result.product_options)
        and all(o.explanation_points for o in result.product_options),
        "all_key_questions_answered": {
            (o.event_id, p.slot_id) for o in result.product_options for p in o.explanation_points
        }
        == {
            (c.event_id, slot.slot_id)
            for c in result.conflicts
            if c.status == "CONFLICT"
            for slot in get_answer_slots(
                c.scenario_code,
                export_receivable_confirmed=request.trade_direction == "EXPORT"
                and request.export_receivable_confirmed,
            )
        },
        "real_retrieval_choices": bool(result.retrieval_trace)
        and all(
            t.get("unique_candidate_count", 0) > 1 and t.get("topic_filter") is None
            for t in result.retrieval_trace
            if t.get("status") == "SEARCHED"
        ),
        "scanned_invoice_ocr": any(d.ocr_backend == "rapidocr_onnx" for d in result.documents),
        "report_created": bool(result.report_path and Path(result.report_path).is_file()),
        "not_failed": result.status != "FAILED",
    }
    payload["verification"]["checks"] = checks
    (output / "live-result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (history / f"{run_id}-result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "llm_calls": result.llm_calls,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "checks": checks,
                "warnings": result.warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
