from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

from app.schemas.document import DocumentAnalysis
from app.schemas.portfolio import PortfolioDocumentResult
from app.services.ingestion.ocr_backend import OcrBackend

DocumentAnalyzer = Callable[..., list[DocumentAnalysis]]


class DocumentAgent:
    """Turn OCR output into the small document contract shared by the workflow."""

    name = "document_agent"

    def __init__(
        self,
        *,
        backend: OcrBackend | None = None,
        analyzer: DocumentAnalyzer | None = None,
    ) -> None:
        self.backend = backend
        self._analyzer = analyzer

    def run(self, paths: Sequence[Path]) -> list[PortfolioDocumentResult]:
        analyzed = self._get_analyzer()(paths, backend=self.backend)
        return [self._to_workflow_result(item) for item in analyzed]

    def _get_analyzer(self) -> DocumentAnalyzer:
        if self._analyzer is None:
            from app.services.ingestion.document_analyzer import analyze_core_documents

            self._analyzer = analyze_core_documents
        return self._analyzer

    @staticmethod
    def _to_workflow_result(item: DocumentAnalysis) -> PortfolioDocumentResult:
        evidence = [
            {
                "field": key,
                "page": field.page,
                "bbox": field.bbox,
                "confidence": field.confidence,
                "raw_label": field.raw_label,
                "raw_value": field.raw_value,
            }
            for key, field in item.fields.items()
        ]
        return PortfolioDocumentResult(
            file_name=unicodedata.normalize("NFC", item.file_name),
            document_type=item.doc_type.value if item.doc_type else "UNKNOWN",
            source_sha256=item.source_sha256,
            status=item.status,
            ocr_backend=item.ocr_backend,
            fields={key: field.value for key, field in item.fields.items()},
            evidence=evidence,
            warnings=item.warnings,
        )
