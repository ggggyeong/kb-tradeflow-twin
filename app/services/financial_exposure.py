from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    CalculationResult,
    Conflict,
    FinancialEvent,
    PaymentObligation,
    Shipment,
    TradeCase,
    TransactionFinancialEventLink,
)
from app.schemas.financial_calendar import (
    FinancialRiskEvaluation,
    FinancialRiskEvaluationInput,
    FinancialRiskEventFact,
)
from app.schemas.workflows import FinancialExposureResult, PaymentGateResult
from app.services.advisory import SHIPMENT_DELAY_SCENARIO_NAME
from app.services.financial_reference import read_transaction_financial_reference
from app.services.financial_risk import DeterministicFinancialRiskEngine
from app.services.observability import local_span
from app.tools.payment_terms import calculate_payment_date

FINANCIAL_EXPOSURE_SCENARIO_NAME = "USER_REPORTED_FINANCIAL_EXPOSURE"
PROACTIVE_RISK_SCENARIO_NAME = "PROACTIVE_FINANCIAL_RISK"
PRIORITY_SOURCE_STATUS = "WORKBOOK_VALIDATED"
CALCULABLE_ANCHOR_TYPES = {"ON_BOARD_DATE"}
SHIPPING_ANCHOR_TYPES = {"B/L_DATE", "ON_BOARD_DATE"}


def _stable_id(prefix: str, value: str, length: int = 20) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:length]}"


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _date_value(value: Any) -> date:
    """Read a date or ISO date-time while preserving the calendar day."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).split("T", 1)[0])


class FinancialExposureService:
    """Own due dates, finance conflicts, priority, persistence, and dedup."""

    def __init__(
        self,
        session: Session,
        risk_engine: DeterministicFinancialRiskEngine | None = None,
    ) -> None:
        self.session = session
        self.risk_engine = risk_engine or DeterministicFinancialRiskEngine()

    def _obligation(self, case_id: str) -> PaymentObligation:
        obligation = self.session.scalar(
            select(PaymentObligation)
            .where(PaymentObligation.case_id == case_id)
            .order_by(PaymentObligation.tranche_index, PaymentObligation.obligation_id)
        )
        if obligation is None:
            raise KeyError(f"No payment obligation for {case_id}")
        return obligation

    def _trade_case(self, case_id: str) -> TradeCase:
        trade_case = self.session.get(TradeCase, case_id)
        if trade_case is None:
            raise KeyError(case_id)
        if trade_case.company_id is None or trade_case.transaction_id is None:
            raise ValueError(f"TradeCase {case_id} lacks canonical company/transaction ownership")
        return trade_case

    def inspect_payment_gate(self, case_id: str) -> PaymentGateResult:
        """Read the DB obligation and fail closed unless every fact is verified."""
        self._trade_case(case_id)
        obligation = self._obligation(case_id)
        missing: list[str] = []
        if obligation.anchor_type_effective == "UNKNOWN":
            missing.append("anchor_type_effective")
        elif obligation.anchor_type_effective not in CALCULABLE_ANCHOR_TYPES:
            missing.append("supported_anchor_source")
        if obligation.tenor_days is None or obligation.tenor_days < 0:
            missing.append("tenor_days")
        if obligation.day_type_effective not in {"CALENDAR", "BUSINESS"}:
            missing.append("day_type_effective")
        permitted = obligation.calculation_allowed == "YES" and obligation.verified and not missing
        question = None
        if not permitted:
            if obligation.calculation_allowed == "AFTER_CONFIRMATION":
                question = (
                    "현재 최소 문서 계약에는 On-board date만 지급 기준일로 저장됩니다. "
                    "지급조건의 B/L DATE를 On-board date로 적용할지 확인해 주세요."
                )
            elif obligation.anchor_type_effective == "B/L_DATE":
                question = (
                    "B/L 발행일은 현재 최소 문서 계약에 저장되지 않습니다. "
                    "On-board date를 지급 기준일로 적용할지 확인해 주세요."
                )
            elif obligation.anchor_type_effective == "INVOICE_DATE":
                question = (
                    "Invoice date는 현재 최소 문서 계약의 계산용 projection에 없습니다. "
                    "지급 기준일을 확인해 주세요."
                )
            else:
                question = "지급 기준일과 기한을 확인해 주세요."
        return PaymentGateResult(
            case_id=case_id,
            obligation_id=obligation.obligation_id,
            raw_text=obligation.raw_text,
            calculation_allowed=obligation.calculation_allowed,
            verified=obligation.verified,
            permitted=permitted,
            anchor_type_effective=obligation.anchor_type_effective,
            tenor_days=obligation.tenor_days,
            day_type_effective=obligation.day_type_effective,
            missing_fields=missing,
            customer_question=question,
            calculate_called=False,
        )

    def _confirm_anchor(self, obligation: PaymentObligation, confirmed_anchor: str) -> None:
        if confirmed_anchor not in CALCULABLE_ANCHOR_TYPES:
            raise ValueError(
                "The minimal document contract supports only ON_BOARD_DATE as a calculated anchor"
            )
        if obligation.calculation_allowed not in {"AFTER_CONFIRMATION", "YES"}:
            raise ValueError("Payment obligation is not eligible for anchor confirmation")
        previous_anchor = obligation.anchor_type_effective
        obligation.anchor_type_effective = confirmed_anchor
        obligation.calculation_allowed = "YES"
        obligation.verified = (
            obligation.tenor_days is not None
            and obligation.day_type_effective in {"CALENDAR", "BUSINESS"}
        )
        obligation.verification_source = "HUMAN_CONFIRMED"
        obligation.verification_metadata_json = {
            **(obligation.verification_metadata_json or {}),
            "previous_anchor_type_effective": previous_anchor,
            "confirmed_anchor_type_effective": confirmed_anchor,
            "minimal_document_anchor_contract": "ON_BOARD_DATE_ONLY",
        }

    def _latest_shipment_scenario(self, case_id: str) -> CalculationResult:
        calculation = self.session.scalar(
            select(CalculationResult)
            .where(
                CalculationResult.case_id == case_id,
                CalculationResult.scenario_name == SHIPMENT_DELAY_SCENARIO_NAME,
            )
            .order_by(
                CalculationResult.created_at.desc(),
                CalculationResult.calculation_id.desc(),
            )
        )
        if calculation is None:
            raise KeyError(f"No shipment delay scenario for {case_id}")
        return calculation

    def _event_facts(self, trade_case: TradeCase) -> list[FinancialRiskEventFact]:
        if trade_case.company_id is None or trade_case.transaction_id is None:
            raise ValueError("Canonical company and transaction IDs are required")
        events = list(
            self.session.scalars(
                select(FinancialEvent)
                .where(FinancialEvent.company_id == trade_case.company_id)
                .order_by(FinancialEvent.event_date, FinancialEvent.financial_event_id)
            )
        )
        links = list(
            self.session.scalars(
                select(TransactionFinancialEventLink).where(
                    TransactionFinancialEventLink.company_id == trade_case.company_id,
                    TransactionFinancialEventLink.transaction_id == trade_case.transaction_id,
                    TransactionFinancialEventLink.case_id == trade_case.case_id,
                )
            )
        )
        links_by_event = {link.financial_event_id: link for link in links}
        return [
            FinancialRiskEventFact(
                financial_event_id=event.financial_event_id,
                company_id=event.company_id,
                event_type_code=event.event_type,
                event_name=event.event_name or event.description or event.event_type,
                event_date=event.event_date,
                amount=event.amount,
                currency=event.currency,
                financial_institution=event.institution,
                is_kb_contract=event.is_kb_contract,
                transaction_event_link_id=(
                    links_by_event[event.financial_event_id].transaction_event_link_id
                    if event.financial_event_id in links_by_event
                    else None
                ),
                link_type=(
                    links_by_event[event.financial_event_id].link_type
                    if event.financial_event_id in links_by_event
                    else None
                ),
                link_status=(
                    links_by_event[event.financial_event_id].link_status
                    if event.financial_event_id in links_by_event
                    else "UNASSESSED"
                ),
                dependency_scope=(
                    links_by_event[event.financial_event_id].dependency_scope or "UNKNOWN"
                    if event.financial_event_id in links_by_event
                    else "UNKNOWN"
                ),
                linked_amount=(
                    links_by_event[event.financial_event_id].linked_amount
                    if event.financial_event_id in links_by_event
                    else None
                ),
                linked_currency=(
                    links_by_event[event.financial_event_id].linked_currency
                    if event.financial_event_id in links_by_event
                    else None
                ),
            )
            for event in events
        ]

    def _persist_snapshot(
        self,
        *,
        trade_case: TradeCase,
        scenario_name: str,
        basis_version: str,
        as_of_date: date,
        source_kind: str,
        source_ref_id: str,
        input_fingerprint: str,
        payload: dict[str, Any],
        is_scenario: bool,
        tool_version: str,
    ) -> tuple[CalculationResult, bool]:
        dedup_key = _stable_digest(
            {
                "case_id": trade_case.case_id,
                "scenario_name": scenario_name,
                "source_kind": source_kind,
                "source_ref_id": source_ref_id,
                "input_fingerprint": input_fingerprint,
            }
        )
        existing = self.session.scalar(
            select(CalculationResult).where(CalculationResult.dedup_key == dedup_key)
        )
        if existing is not None:
            return existing, True

        calculation_id = _stable_id("RISK", dedup_key)
        persisted_payload = {
            **payload,
            "calculation_id": calculation_id,
            "basis_version": basis_version,
            "conflict_count": len(payload.get("conflicts", [])),
        }
        calculation = CalculationResult(
            calculation_id=calculation_id,
            case_id=trade_case.case_id,
            company_id=trade_case.company_id,
            transaction_id=trade_case.transaction_id,
            basis_version=basis_version,
            scenario_name=scenario_name,
            as_of_date=as_of_date,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            input_fingerprint=input_fingerprint,
            dedup_key=dedup_key,
            result_json=persisted_payload,
            audit_json={
                "calculation_owner": "financial_exposure",
                "priority_source_sha256": (self.risk_engine.policy.source_sha256),
                "priority_rule_version": self.risk_engine.policy.version,
                "input_fingerprint": input_fingerprint,
                "dedup_key": dedup_key,
                "source_kind": source_kind,
                "source_ref_id": source_ref_id,
                "append_only_snapshot": True,
            },
            tool_version=tool_version,
            is_scenario=is_scenario,
        )
        self.session.add(calculation)
        self.session.flush()
        for item in persisted_payload.get("conflicts", []):
            conflict_id = _stable_id(
                "CONFLICT",
                f"{calculation_id}:{item['comparison_id']}",
            )
            if self.session.get(Conflict, conflict_id) is not None:
                continue
            self.session.add(
                Conflict(
                    conflict_id=conflict_id,
                    calculation_id=calculation_id,
                    financial_event_id=str(item["financial_event_id"]),
                    transaction_event_link_id=item.get("transaction_event_link_id"),
                    gap_days=int(item.get("gap_days", 0)),
                    priority=str(
                        item.get(
                            "response_priority_level",
                            item.get("priority", "P4"),
                        )
                    ),
                    priority_rank=(
                        int(item["priority_rank"])
                        if item.get("priority_rank") is not None
                        else None
                    ),
                    conflict_origin=item.get("conflict_origin"),
                    link_status=item.get("link_status"),
                    impact_level=item.get("impact_level"),
                    days_until_event=(
                        int(item["days_until_event"])
                        if item.get("days_until_event") is not None
                        else None
                    ),
                    same_day_flag=bool(item.get("same_day_flag", False)),
                    reason=str(item.get("reason", "")),
                    details_json=dict(item),
                )
            )
        self.session.flush()
        return calculation, False

    def _persist_evaluation(
        self,
        evaluation: FinancialRiskEvaluation,
        trade_case: TradeCase,
        *,
        scenario_name: str,
        basis_version: str,
        is_scenario: bool,
        tool_version: str,
        payload_extra: dict[str, Any] | None = None,
    ) -> tuple[CalculationResult, bool]:
        payload = evaluation.model_dump(mode="json")
        if payload_extra:
            payload.update(payload_extra)
        return self._persist_snapshot(
            trade_case=trade_case,
            scenario_name=scenario_name,
            basis_version=basis_version,
            as_of_date=evaluation.as_of_date,
            source_kind=evaluation.source_kind,
            source_ref_id=evaluation.source_ref_id,
            input_fingerprint=evaluation.input_fingerprint,
            payload=payload,
            is_scenario=is_scenario,
            tool_version=tool_version,
        )

    @traceable(name="run_proactive_risk_scan", run_type="chain")
    def run_proactive_risk_scan(
        self,
        case_id: str,
        as_of_date: date,
        request_id: str,
    ) -> dict[str, Any]:
        """Persist a live scan, including unresolved shipping-anchor risk frontiers."""
        trade_case = self._trade_case(case_id)
        shipment = self.session.scalar(select(Shipment).where(Shipment.case_id == case_id))
        if shipment is None:
            raise KeyError(f"No shipment for {case_id}")
        events = self._event_facts(trade_case)
        try:
            obligation = self._obligation(case_id)
            gate = self.inspect_payment_gate(case_id)
        except KeyError:
            obligation = None
            gate = None

        planned_due: date | None = None
        revised_due: date | None = None
        tenor_days: int | None = None
        anchor_resolved = False
        shipping_anchor_eligible = bool(
            obligation is not None
            and obligation.tenor_days is not None
            and obligation.tenor_days >= 0
            and obligation.day_type_effective in {"CALENDAR", "BUSINESS"}
            and obligation.calculation_allowed in {"YES", "AFTER_CONFIRMATION"}
            and (
                obligation.anchor_type_effective in SHIPPING_ANCHOR_TYPES
                or obligation.anchor_type_extracted == "B/L_DATE"
            )
        )
        if shipping_anchor_eligible and obligation is not None:
            tenor_days = obligation.tenor_days
            if shipment.etd is not None:
                planned_due = date.fromisoformat(
                    str(
                        calculate_payment_date.invoke(
                            {
                                "anchor_date": shipment.etd,
                                "tenor_days": tenor_days,
                                "day_type": obligation.day_type_effective,
                            }
                        )["payment_date"]
                    )
                )
            actual_anchor: date | None = None
            if (
                gate is not None
                and gate.permitted
                and obligation.anchor_type_effective == "ON_BOARD_DATE"
            ):
                actual_anchor = shipment.on_board_date
            if actual_anchor is not None:
                anchor_resolved = True
                revised_due = date.fromisoformat(
                    str(
                        calculate_payment_date.invoke(
                            {
                                "anchor_date": actual_anchor,
                                "tenor_days": obligation.tenor_days,
                                "day_type": obligation.day_type_effective,
                            }
                        )["payment_date"]
                    )
                )

        source_ref_id = (
            f"{case_id}:{as_of_date.isoformat()}:shipment-state-v{shipment.state_version}"
        )
        financial_reference = read_transaction_financial_reference(self.session, trade_case)
        evaluation_input = FinancialRiskEvaluationInput(
            case_id=case_id,
            company_id=str(trade_case.company_id),
            transaction_id=str(trade_case.transaction_id),
            as_of_date=as_of_date,
            source_kind="MONITORING_SCAN",
            source_ref_id=source_ref_id,
            payment_obligation_id=(obligation.obligation_id if obligation else None),
            effective_anchor_type=(obligation.anchor_type_effective if obligation else None),
            day_type_effective=(obligation.day_type_effective if obligation else None),
            planned_anchor_basis=("BOOKING_ETD_PROXY" if planned_due else None),
            revised_anchor_basis=("BILL_OF_LADING_ON_BOARD_DATE" if revised_due else None),
            planned_due_date=planned_due,
            revised_due_date=revised_due,
            tenor_days=tenor_days,
            anchor_resolved=anchor_resolved,
            expected_receipt_amount=financial_reference["amount"],
            expected_receipt_currency=financial_reference["currency"],
            events=events,
        )
        with local_span(
            self.session,
            request_id,
            "workflow",
            "run_proactive_risk_scan",
            {
                "case_id": case_id,
                "as_of_date": as_of_date.isoformat(),
                "shipment_state_version": shipment.state_version,
            },
        ) as span:
            evaluation = self.risk_engine.evaluate(evaluation_input)
            basis_version = (
                f"{trade_case.basis_version}:state-v{shipment.state_version}:"
                f"{self.risk_engine.policy.version}"
            )
            calculation, deduplicated = self._persist_evaluation(
                evaluation,
                trade_case,
                scenario_name=PROACTIVE_RISK_SCENARIO_NAME,
                basis_version=basis_version,
                is_scenario=False,
                tool_version="financial-risk.v19.0",
                payload_extra={
                    "payment_gate": gate.model_dump(mode="json") if gate else None,
                    "payment_basis": {
                        "obligation_id": obligation.obligation_id if obligation else None,
                        "effective_anchor_type": (
                            obligation.anchor_type_effective if obligation else None
                        ),
                        "day_type_effective": (
                            obligation.day_type_effective if obligation else None
                        ),
                        "planned_anchor_basis": "BOOKING_ETD_PROXY" if planned_due else None,
                        "revised_anchor_basis": (
                            "BILL_OF_LADING_ON_BOARD_DATE" if revised_due else None
                        ),
                    },
                },
            )
            snapshot = self._snapshot_from_calculation(calculation)
            snapshot["deduplicated"] = deduplicated
            span.update(
                {
                    "status": evaluation.status,
                    "conflict_count": len(evaluation.conflicts),
                    "deduplicated": deduplicated,
                    "calculation_owner": "financial_exposure",
                }
            )
            return snapshot

    @traceable(name="calculate_financial_exposure", run_type="chain")
    def calculate_financial_exposure(
        self,
        case_id: str,
        request_id: str,
        confirmed_anchor: str | None = None,
    ) -> FinancialExposureResult:
        """Evaluate canonical shipment-delay scenarios without mutating shipment facts."""
        shipment_calculation = self._latest_shipment_scenario(case_id)
        obligation = self._obligation(case_id)
        trade_case = self._trade_case(case_id)
        if confirmed_anchor is not None:
            self._confirm_anchor(obligation, confirmed_anchor)
        gate = self.inspect_payment_gate(case_id)
        if not gate.permitted:
            raise ValueError(
                f"Payment calculation gate denied: {gate.customer_question or gate.missing_fields}"
            )
        if obligation.anchor_type_effective != "ON_BOARD_DATE":
            raise ValueError(
                "Shipment-delay exposure requires ON_BOARD_DATE under the minimal document contract"
            )
        if obligation.tenor_days is None or obligation.day_type_effective is None:
            raise ValueError("Verified tenor and day type are required")

        events = self._event_facts(trade_case)
        if not any(event.transaction_event_link_id is not None for event in events):
            raise ValueError("At least one transaction-linked financial event is required")
        financial_reference = read_transaction_financial_reference(self.session, trade_case)
        shipment_scenarios = list(shipment_calculation.result_json["scenarios"])
        if not shipment_scenarios:
            raise ValueError("Shipment calculation contains no delay scenarios")
        reported_value = shipment_calculation.result_json.get("reported_at")
        if reported_value is None:
            raise ValueError("Shipment scenario lacks reported_at")
        reported_date = _date_value(reported_value)
        planned_value = shipment_calculation.result_json.get("planned_departure_date")
        if planned_value is None:
            raise ValueError("Shipment scenario lacks planned_departure_date")
        planned_anchor = _date_value(planned_value)
        planned_payment = calculate_payment_date.invoke(
            {
                "anchor_date": planned_anchor,
                "tenor_days": obligation.tenor_days,
                "day_type": obligation.day_type_effective,
            }
        )
        planned_due = date.fromisoformat(str(planned_payment["payment_date"]))

        with local_span(
            self.session,
            request_id,
            "workflow",
            "calculate_financial_exposure",
            {
                "case_id": case_id,
                "shipment_calculation_id": shipment_calculation.calculation_id,
            },
        ) as span:
            scenarios: list[dict[str, Any]] = []
            comparisons: list[dict[str, Any]] = []
            conflicts: list[dict[str, Any]] = []
            data_actions_by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
            evaluation_fingerprints: list[str] = []
            for shipment_scenario in shipment_scenarios:
                raw_anchor_date = shipment_scenario.get("revised_expected_departure_date")
                if raw_anchor_date is None:
                    raise ValueError("Shipment scenario lacks revised_expected_departure_date")
                anchor_date = _date_value(raw_anchor_date)
                payment = calculate_payment_date.invoke(
                    {
                        "anchor_date": anchor_date,
                        "tenor_days": obligation.tenor_days,
                        "day_type": obligation.day_type_effective,
                    }
                )
                revised_due = date.fromisoformat(str(payment["payment_date"]))
                if shipment_scenario.get("delay_days") is None:
                    raise ValueError("Shipment scenario lacks delay_days")
                days = int(shipment_scenario["delay_days"])
                evaluation = self.risk_engine.evaluate(
                    FinancialRiskEvaluationInput(
                        case_id=case_id,
                        company_id=str(trade_case.company_id),
                        transaction_id=str(trade_case.transaction_id),
                        as_of_date=reported_date,
                        source_kind="USER_REPORTED_DELAY",
                        source_ref_id=f"{shipment_calculation.calculation_id}:{days}",
                        payment_obligation_id=obligation.obligation_id,
                        effective_anchor_type=obligation.anchor_type_effective,
                        day_type_effective=obligation.day_type_effective,
                        planned_anchor_basis="BOOKING_ETD_PROXY",
                        revised_anchor_basis="REVISED_EXPECTED_DEPARTURE_PROXY",
                        planned_due_date=planned_due,
                        revised_due_date=revised_due,
                        tenor_days=obligation.tenor_days,
                        anchor_resolved=True,
                        expected_receipt_amount=financial_reference["amount"],
                        expected_receipt_currency=financial_reference["currency"],
                        events=events,
                    )
                )
                evaluation_fingerprints.append(evaluation.input_fingerprint)
                for item in evaluation.comparisons:
                    comparison = item.model_dump(mode="json")
                    comparison["scenario_delay_days"] = days
                    comparisons.append(comparison)
                for item in evaluation.conflicts:
                    conflict = item.model_dump(mode="json")
                    conflict["scenario_delay_days"] = days
                    conflicts.append(conflict)
                for action in evaluation.data_actions:
                    link_id = str(action["transaction_event_link_id"])
                    action_key = (
                        link_id,
                        tuple(str(item) for item in action["missing_fields"]),
                    )
                    data_actions_by_key[action_key] = dict(action)
                normalized_scenario = dict(shipment_scenario)
                normalized_scenario.update(
                    {
                        "effective_anchor_type": obligation.anchor_type_effective,
                        "calculation_anchor_date": anchor_date.isoformat(),
                        "calculation_anchor_basis": (
                            "REVISED_EXPECTED_DEPARTURE_PROXY_FOR_ON_BOARD_DATE"
                        ),
                        "calculated_payment_date": payment["payment_date"],
                        "expected_receipt_date": payment["payment_date"],
                        "payment_source": "calculate_payment_date",
                    }
                )
                scenarios.append(normalized_scenario)

            conflicts.sort(
                key=lambda item: (
                    int(item["days_until_event"]),
                    int(item["scenario_delay_days"]),
                    str(item["financial_event_id"]),
                )
            )
            for rank, conflict in enumerate(conflicts, start=1):
                conflict["priority_rank"] = rank
            highest_priority = str(conflicts[0]["response_priority_level"]) if conflicts else None
            input_fingerprint = _stable_digest(
                {
                    "shipment_calculation_id": shipment_calculation.calculation_id,
                    "obligation_id": obligation.obligation_id,
                    "anchor_type_effective": obligation.anchor_type_effective,
                    "evaluation_fingerprints": evaluation_fingerprints,
                }
            )
            basis_version = (
                f"{shipment_calculation.basis_version}:"
                f"{obligation.obligation_id}:{obligation.anchor_type_effective}:"
                f"{self.risk_engine.policy.version}"
            )
            expected_date = max(str(scenario["calculated_payment_date"]) for scenario in scenarios)
            trace_sequence = [
                "inspect_payment_gate",
                "read_company_financial_events_and_links",
                "calculate_payment_date",
                "evaluate_shared_financial_risk",
                "persist_calculation_result_and_conflicts",
            ]
            result_json = {
                "case_id": case_id,
                "company_id": trade_case.company_id,
                "transaction_id": trade_case.transaction_id,
                "as_of_date": reported_date.isoformat(),
                "source_kind": "USER_REPORTED_DELAY",
                "source_ref_id": shipment_calculation.calculation_id,
                "shipment_calculation_id": shipment_calculation.calculation_id,
                "payment_gate": gate.model_dump(mode="json"),
                "payment_basis": {
                    "obligation_id": obligation.obligation_id,
                    "effective_anchor_type": obligation.anchor_type_effective,
                    "day_type_effective": obligation.day_type_effective,
                    "planned_anchor_basis": "BOOKING_ETD_PROXY",
                    "revised_anchor_basis": "REVISED_EXPECTED_DEPARTURE_PROXY",
                },
                "expected_receipt": {
                    "date": expected_date,
                    "amount": financial_reference["amount"],
                    "currency": financial_reference["currency"],
                },
                "highest_priority": highest_priority,
                "scenarios": scenarios,
                "comparisons": comparisons,
                "conflicts": conflicts,
                "data_actions": list(data_actions_by_key.values()),
                "priority_rule_version": self.risk_engine.policy.version,
                "input_fingerprint": input_fingerprint,
                "priority_source_status": PRIORITY_SOURCE_STATUS,
                "trace_sequence": trace_sequence,
            }
            calculation, _ = self._persist_snapshot(
                trade_case=trade_case,
                scenario_name=FINANCIAL_EXPOSURE_SCENARIO_NAME,
                basis_version=basis_version,
                as_of_date=reported_date,
                source_kind="USER_REPORTED_DELAY",
                source_ref_id=shipment_calculation.calculation_id,
                input_fingerprint=input_fingerprint,
                payload=result_json,
                is_scenario=True,
                tool_version="financial-risk.v19.0",
            )
            persisted = calculation.result_json
            result = FinancialExposureResult(
                case_id=case_id,
                calculation_id=calculation.calculation_id,
                shipment_calculation_id=shipment_calculation.calculation_id,
                basis_version=calculation.basis_version,
                effective_anchor_type=obligation.anchor_type_effective,
                scenario_count=len(persisted["scenarios"]),
                conflict_count=len(persisted["conflicts"]),
                scenarios=persisted["scenarios"],
                conflicts=persisted["conflicts"],
                data_actions=list(persisted.get("data_actions", [])),
                priority_source_status=PRIORITY_SOURCE_STATUS,
                trace_sequence=list(persisted["trace_sequence"]),
            )
            span.update(
                {
                    "scenario_count": result.scenario_count,
                    "conflict_count": result.conflict_count,
                    "calculation_owner": "financial_exposure",
                }
            )
            return result

    @staticmethod
    def _snapshot_from_calculation(
        calculation: CalculationResult,
    ) -> dict[str, Any]:
        payload = calculation.result_json
        conflicts = []
        for raw in payload.get("conflicts", []):
            item = dict(raw)
            if item.get("response_priority_level"):
                item["priority"] = item["response_priority_level"]
            conflicts.append(item)
        return {
            "case_id": calculation.case_id,
            "company_id": calculation.company_id,
            "transaction_id": calculation.transaction_id,
            "calculation_id": calculation.calculation_id,
            "basis_version": calculation.basis_version,
            "as_of_date": (
                calculation.as_of_date.isoformat()
                if calculation.as_of_date
                else payload.get("as_of_date")
            ),
            "source_kind": calculation.source_kind,
            "source_ref_id": calculation.source_ref_id,
            "payment_gate": payload.get("payment_gate"),
            "payment_basis": payload.get("payment_basis", {}),
            "expected_receipt": payload.get(
                "expected_receipt",
                {"date": None, "amount": None, "currency": None},
            ),
            "highest_priority": payload.get("highest_priority"),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "priority_rule_version": payload.get("priority_rule_version"),
            "input_fingerprint": calculation.input_fingerprint,
            "status": payload.get("status", "EVALUATED"),
            "data_actions": list(payload.get("data_actions", [])),
        }

    def get_risk_snapshot(
        self,
        case_id: str,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        """Return the latest immutable risk snapshot, optionally on/before a date."""
        trade_case = self._trade_case(case_id)
        statement = select(CalculationResult).where(
            CalculationResult.case_id == case_id,
            CalculationResult.company_id == trade_case.company_id,
            CalculationResult.transaction_id == trade_case.transaction_id,
            CalculationResult.source_kind.in_(["MONITORING_SCAN", "USER_REPORTED_DELAY"]),
        )
        if as_of_date is not None:
            statement = statement.where(CalculationResult.as_of_date <= as_of_date)
        calculation = self.session.scalar(
            statement.order_by(
                CalculationResult.as_of_date.desc(),
                CalculationResult.created_at.desc(),
                CalculationResult.calculation_id.desc(),
            )
        )
        if calculation is None:
            raise KeyError(f"No financial risk snapshot for {case_id}")
        return self._snapshot_from_calculation(calculation)
