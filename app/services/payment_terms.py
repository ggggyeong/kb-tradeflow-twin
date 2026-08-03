from __future__ import annotations

import re
from datetime import date

from app.domain_inputs.providers import payment_terms_contract
from app.schemas.payment_terms import ParsedPaymentObligation, PaymentTermExample
from app.tools.payment_terms import PaymentDateToolResult, calculate_payment_date_value


class PaymentCalculationGateResult(ParsedPaymentObligation):
    """Parsed obligation plus proof of whether the calculator was called."""

    calculate_called: bool = False
    computed_payment_date: date | None = None


def _from_example(example: PaymentTermExample, tranche_index: int) -> ParsedPaymentObligation:
    missing: list[str] = []
    if example.anchor_type_effective == "UNKNOWN":
        missing.append("anchor_type_effective")
    if example.tenor_days is None and example.calculation_allowed == "AFTER_CONFIRMATION":
        missing.append("tenor_days")
    question = example.customer_question
    return ParsedPaymentObligation(
        tranche_index=tranche_index,
        raw_text=example.raw_text,
        payment_method=example.payment_method,
        credit_term_type=example.credit_term_type or "UNKNOWN",
        lc_type=example.lc_type,
        anchor_type_extracted=example.anchor_type_extracted,
        anchor_type_effective=example.anchor_type_effective,
        tenor_days=example.tenor_days,
        day_type_extracted=example.day_type_extracted,
        day_type_effective=example.day_type_effective,
        payment_ratio=example.payment_ratio,
        advance_or_deferred=example.advance_or_deferred,
        calculation_allowed=example.calculation_allowed,
        confidence_flag=("CONFIRMED" if example.calculation_allowed == "YES" else "NEEDS_REVIEW"),
        document_stage=example.document_stage,
        real_world_status=example.real_world_status,
        verified=example.calculation_allowed == "YES",
        missing_fields=missing,
        customer_question=question,
    )


def parse_payment_terms(raw_text: str) -> list[ParsedPaymentObligation]:
    """Parse reviewed rows and their bounded workbook-defined tenor families."""
    contract = payment_terms_contract()
    exact = contract.exact_examples(raw_text)
    if exact:
        return [
            _from_example(example, tranche_index)
            for tranche_index, example in enumerate(exact, start=1)
        ]
    normalized = " ".join(raw_text.upper().split())
    tt_after_bl = re.fullmatch(
        r"T/?T\s+(\d+)\s+DAYS?\s+AFTER\s+B/?L\s+DATE",
        normalized,
    )
    if tt_after_bl:
        return [
            ParsedPaymentObligation(
                raw_text=raw_text,
                payment_method="TT",
                anchor_type_extracted="B/L_DATE",
                anchor_type_effective="UNKNOWN",
                tenor_days=int(tt_after_bl.group(1)),
                day_type_extracted="UNSPECIFIED",
                day_type_effective="CALENDAR",
                advance_or_deferred="DEFERRED",
                calculation_allowed="AFTER_CONFIRMATION",
                confidence_flag="NEEDS_REVIEW",
                real_world_status="WORKBOOK_DERIVED_FAMILY",
                verified=False,
                missing_fields=["anchor_type_effective"],
                customer_question=(
                    "B/L DATE를 B/L 발행일과 On-board date 중 어느 기준으로 적용할지 확인해 주세요."
                ),
            )
        ]
    usance_after_bl = re.fullmatch(
        r"USANCE\s+L/?C\s+(\d+)\s+DAYS?\s+AFTER\s+B/?L\s+DATE",
        normalized,
    )
    if usance_after_bl:
        return [
            ParsedPaymentObligation(
                raw_text=raw_text,
                payment_method="LC",
                credit_term_type="USANCE",
                lc_type="USANCE",
                anchor_type_extracted="B/L_DATE",
                anchor_type_effective="ON_BOARD_DATE",
                tenor_days=int(usance_after_bl.group(1)),
                day_type_extracted="UNSPECIFIED",
                day_type_effective="CALENDAR",
                advance_or_deferred="DEFERRED",
                calculation_allowed="YES",
                confidence_flag="CONFIRMED",
                real_world_status="WORKBOOK_DERIVED_FAMILY",
                verified=True,
            )
        ]
    return [
        ParsedPaymentObligation(
            raw_text=raw_text,
            payment_method="UNKNOWN",
            calculation_allowed="NO",
            confidence_flag="NEEDS_REVIEW",
            verified=False,
            missing_fields=["payment_method", "anchor_type_effective", "tenor_days"],
            customer_question=("결제 방식, 지급 기준일, 지급기한을 확인해 주세요."),
        )
    ]


def confirm_anchor(
    obligation: ParsedPaymentObligation, confirmed_anchor: str
) -> ParsedPaymentObligation:
    """Apply a user's anchor answer without mutating the original object."""
    if obligation.calculation_allowed != "AFTER_CONFIRMATION":
        raise ValueError("This obligation is not waiting for anchor confirmation")
    if confirmed_anchor not in {"B/L_DATE", "ON_BOARD_DATE", "INVOICE_DATE"}:
        raise ValueError(f"Unsupported confirmed anchor: {confirmed_anchor}")
    return obligation.model_copy(
        update={
            "anchor_type_effective": confirmed_anchor,
            "calculation_allowed": "YES",
            "verified": obligation.tenor_days is not None
            and obligation.day_type_effective in {"CALENDAR", "BUSINESS"},
            "missing_fields": [
                field for field in obligation.missing_fields if field != "anchor_type_effective"
            ],
            "customer_question": None,
            "confidence_flag": "CONFIRMED",
        }
    )


def compute_if_allowed(
    obligation: ParsedPaymentObligation, anchor_date: date | None
) -> PaymentCalculationGateResult:
    """Call the calculator only when every workbook-derived gate is satisfied."""
    base = obligation.model_dump()
    if (
        obligation.calculation_allowed != "YES"
        or not obligation.verified
        or anchor_date is None
        or obligation.tenor_days is None
        or obligation.day_type_effective not in {"CALENDAR", "BUSINESS"}
    ):
        return PaymentCalculationGateResult(
            **base, calculate_called=False, computed_payment_date=None
        )
    result: PaymentDateToolResult = calculate_payment_date_value(
        anchor_date, obligation.tenor_days, obligation.day_type_effective
    )
    return PaymentCalculationGateResult(
        **base,
        calculate_called=True,
        computed_payment_date=result.payment_date,
    )
