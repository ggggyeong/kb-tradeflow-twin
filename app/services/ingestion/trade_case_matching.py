from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.schemas.field_contract import DocumentType, FieldRegistry

ValueReader = Callable[[Any, str], str | None]

HIGH_CONFIDENCE_SCORE = 85.0
MINIMUM_HIGH_CONFIDENCE_MARGIN = 10.0
CONFIRMATION_SCORE = 65.0


@dataclass(frozen=True)
class MatchRule:
    """One deterministic equality check contributing to a document-pair score."""

    left_field: str
    right_field: str
    weight: float


@dataclass(frozen=True)
class MatchEvidence:
    """Privacy-safe evidence showing which contracted fields supported a match."""

    left_field: str
    right_field: str
    weight: float
    matched: bool

    def as_trace(self) -> dict[str, Any]:
        return {
            "left_field": self.left_field,
            "right_field": self.right_field,
            "weight": self.weight,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class RelationScore:
    """Score and field-level evidence for one document relationship."""

    relation: str
    score: float
    maximum_score: float
    evidence: tuple[MatchEvidence, ...]

    def as_trace(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "score": self.score,
            "maximum_score": self.maximum_score,
            "evidence": [item.as_trace() for item in self.evidence],
        }


@dataclass(frozen=True)
class CandidateScore:
    """Conservative score for assigning one document to a TradeCase candidate."""

    score: float
    relations: tuple[RelationScore, ...]

    def as_trace(self) -> list[dict[str, Any]]:
        return [relation.as_trace() for relation in self.relations]


INVOICE_BOOKING_RULES = (
    MatchRule("seller", "shipper", 50.0),
    MatchRule("description_of_goods", "commodity", 40.0),
)

BILL_OF_LADING_BOOKING_RULES = (
    MatchRule("bl_no", "booking_bl_no", 60.0),
    MatchRule("shipper", "shipper", 20.0),
    MatchRule("port_of_loading", "port_of_loading", 5.0),
    MatchRule("port_of_discharge", "port_of_discharge", 5.0),
    MatchRule("vessel_name", "vessel_name", 5.0),
    MatchRule("voyage_no", "voyage_no", 5.0),
)

BILL_OF_LADING_INVOICE_RULES = (
    MatchRule("shipper", "seller", 45.0),
    MatchRule("consignee", "buyer", 40.0),
)

MATCHING_FIELD_REQUIREMENTS: dict[DocumentType, frozenset[str]] = {
    DocumentType.BOOKING_CONFIRMATION: frozenset(
        {
            "booking_bl_no",
            "shipper",
            "commodity",
            "port_of_loading",
            "port_of_discharge",
            "vessel_name",
            "voyage_no",
        }
    ),
    DocumentType.COMMERCIAL_INVOICE: frozenset({"seller", "buyer", "description_of_goods"}),
    DocumentType.BILL_OF_LADING: frozenset(
        {
            "bl_no",
            "shipper",
            "consignee",
            "port_of_loading",
            "port_of_discharge",
            "vessel_name",
            "voyage_no",
        }
    ),
}


def validate_matching_field_contract(registry: FieldRegistry) -> None:
    """Fail closed if the reviewed field registry drops a matching support field."""
    missing: list[str] = []
    for doc_type, required_fields in MATCHING_FIELD_REQUIREMENTS.items():
        for field in sorted(required_fields - registry.exact_keys(doc_type)):
            missing.append(f"{doc_type.value}.{field}")
    if missing:
        raise ValueError(f"Trade-case matching fields missing from contract: {missing}")


def _normalized_equal(left: str | None, right: str | None) -> bool:
    """Compare controlled demo values without fuzzy or model-based inference."""
    return bool(
        left and right and " ".join(left.upper().split()) == " ".join(right.upper().split())
    )


def _score_relation(
    relation: str,
    left: Any,
    right: Any,
    rules: tuple[MatchRule, ...],
    read_value: ValueReader,
) -> RelationScore:
    evidence = tuple(
        MatchEvidence(
            left_field=rule.left_field,
            right_field=rule.right_field,
            weight=rule.weight,
            matched=_normalized_equal(
                read_value(left, rule.left_field),
                read_value(right, rule.right_field),
            ),
        )
        for rule in rules
    )
    return RelationScore(
        relation=relation,
        score=sum(item.weight for item in evidence if item.matched),
        maximum_score=sum(rule.weight for rule in rules),
        evidence=evidence,
    )


def score_invoice_to_booking(
    invoice: Any,
    booking: Any,
    read_value: ValueReader,
) -> CandidateScore:
    """Score Invoice↔Booking by party and goods equality."""
    relation = _score_relation(
        "INVOICE_BOOKING",
        invoice,
        booking,
        INVOICE_BOOKING_RULES,
        read_value,
    )
    return CandidateScore(score=relation.score, relations=(relation,))


def score_bill_of_lading_to_case(
    bill_of_lading: Any,
    booking: Any | None,
    invoice: Any | None,
    read_value: ValueReader,
) -> CandidateScore:
    """Score B/L against every available case document and keep the safer best relation."""
    relations: list[RelationScore] = []
    if booking is not None:
        relations.append(
            _score_relation(
                "BILL_OF_LADING_BOOKING",
                bill_of_lading,
                booking,
                BILL_OF_LADING_BOOKING_RULES,
                read_value,
            )
        )
    if invoice is not None:
        relations.append(
            _score_relation(
                "BILL_OF_LADING_INVOICE",
                bill_of_lading,
                invoice,
                BILL_OF_LADING_INVOICE_RULES,
                read_value,
            )
        )
    return CandidateScore(
        score=max((relation.score for relation in relations), default=0.0),
        relations=tuple(relations),
    )


def matching_verdict(score: float, margin: float) -> str:
    """Convert a candidate score and top-two margin into a deterministic verdict."""
    if score >= HIGH_CONFIDENCE_SCORE and margin >= MINIMUM_HIGH_CONFIDENCE_MARGIN:
        return "HIGH"
    if score >= CONFIRMATION_SCORE:
        return "CONFIRM"
    return "LOW"
