from __future__ import annotations

from dataclasses import dataclass

from app.schemas.document import ClassificationResult, DocumentType


@dataclass(frozen=True)
class TemplateProfile:
    """Small signature contract for one portfolio document type."""

    doc_type: DocumentType
    required_signatures: tuple[str, ...]
    supporting_signatures: tuple[str, ...]


PROFILES = [
    TemplateProfile(
        doc_type=DocumentType.BOOKING_CONFIRMATION,
        required_signatures=("BOOKING RECEIPT NOTICE", "BOOKING NO"),
        supporting_signatures=("PROFORMA 1ST VESSEL ETD", "TRUNK VESSEL"),
    ),
    TemplateProfile(
        doc_type=DocumentType.BILL_OF_LADING,
        required_signatures=("BILL OF LADING", "B/L NO"),
        supporting_signatures=("LADEN ON BOARD VESSEL", "PORT OF DISCHARGE"),
    ),
    TemplateProfile(
        doc_type=DocumentType.COMMERCIAL_INVOICE,
        required_signatures=("COMMERCIAL INVOICE", "INVOICE NO"),
        supporting_signatures=("TERMS OF DELIVERY AND PAYMENT", "SIGNED BY"),
    ),
]


def classify_trade_document(text: str) -> ClassificationResult:
    """Classify the three supported trade documents with visible signatures."""
    normalized = " ".join(text.upper().replace("\u2019", "'").split())
    candidates: list[tuple[float, TemplateProfile, list[str]]] = []
    for profile in PROFILES:
        required_hits = [
            signature for signature in profile.required_signatures if signature in normalized
        ]
        supporting_hits = [
            signature for signature in profile.supporting_signatures if signature in normalized
        ]
        score = (len(required_hits) * 0.4 + len(supporting_hits) * 0.1) / (
            len(profile.required_signatures) * 0.4 + len(profile.supporting_signatures) * 0.1
        )
        candidates.append((score, profile, required_hits + supporting_hits))
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best, signals = candidates[0]
    margin = best_score - candidates[1][0]
    required_complete = all(signature in normalized for signature in best.required_signatures)
    if required_complete and best_score >= 0.8 and margin >= 0.3:
        return ClassificationResult(
            status="AUTO_CONFIRMED",
            doc_type=best.doc_type,
            score=best_score,
            signals=signals,
        )
    if best_score >= 0.35:
        return ClassificationResult(
            status="REVIEW_REQUIRED",
            doc_type=best.doc_type,
            score=best_score,
            signals=signals,
        )
    return ClassificationResult(
        status="UNSUPPORTED",
        doc_type=None,
        score=best_score,
        signals=signals,
    )


# Kept as a small import-compatible name while callers move to the portfolio API.
classify_fixed_template = classify_trade_document
