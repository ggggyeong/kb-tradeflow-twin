from __future__ import annotations

from dataclasses import dataclass

from app.schemas.document import ClassificationResult
from app.schemas.field_contract import DocumentType


@dataclass(frozen=True)
class TemplateProfile:
    """Signature contract for one fixed template."""

    template_id: str
    doc_type: DocumentType
    required_signatures: tuple[str, ...]
    supporting_signatures: tuple[str, ...]


PROFILES = [
    TemplateProfile(
        template_id="BOOKING_ONE_V1",
        doc_type=DocumentType.BOOKING_CONFIRMATION,
        required_signatures=("BOOKING RECEIPT NOTICE", "BOOKING NO"),
        supporting_signatures=("PROFORMA 1ST VESSEL ETD", "TRUNK VESSEL"),
    ),
    TemplateProfile(
        template_id="BILL_OF_LADING_V1",
        doc_type=DocumentType.BILL_OF_LADING,
        required_signatures=("BILL OF LADING", "B/L NO"),
        supporting_signatures=("LADEN ON BOARD VESSEL", "PORT OF DISCHARGE"),
    ),
    TemplateProfile(
        template_id="COMMERCIAL_INVOICE_V1",
        doc_type=DocumentType.COMMERCIAL_INVOICE,
        required_signatures=("COMMERCIAL INVOICE", "INVOICE NO"),
        supporting_signatures=("TERMS OF DELIVERY AND PAYMENT", "SIGNED BY"),
    ),
]


def classify_fixed_template(text: str) -> ClassificationResult:
    """Classify only the three supported templates without any model call."""
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
            template_id=best.template_id,
            score=best_score,
            signals=signals,
            model_calls=0,
        )
    if best_score >= 0.35:
        return ClassificationResult(
            status="CONFIRM_REQUIRED",
            doc_type=best.doc_type,
            template_id=best.template_id,
            score=best_score,
            signals=signals,
            model_calls=0,
        )
    return ClassificationResult(
        status="UNSUPPORTED",
        doc_type=None,
        template_id=None,
        score=best_score,
        signals=signals,
        model_calls=0,
    )
