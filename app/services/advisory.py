from __future__ import annotations

import hashlib
from datetime import date, timedelta

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CalculationResult, Shipment, ShipmentEvent, TradeCase
from app.schemas.workflows import ShipmentDelayScenarioResult
from app.services.observability import local_span

SHIPMENT_DELAY_SCENARIO_NAME = "SHIPMENT_DELAY_SCENARIOS"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


class AdvisoryService:
    """Persist shipment-owned expected-departure scenarios without finance calculations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @traceable(name="record_shipment_delay_scenarios", run_type="chain")
    def record_shipment_delay_scenarios(
        self,
        case_id: str,
        reported_at: date,
        request_id: str,
        expected_delay_days: list[int],
    ) -> ShipmentDelayScenarioResult:
        """Record report time separately from Booking-ETD expected departure dates."""
        if not expected_delay_days or any(days < 0 or days > 365 for days in expected_delay_days):
            raise ValueError("expected_delay_days must contain values between 0 and 365")
        expected_delay_days = sorted(set(expected_delay_days))

        trade_case = self.session.get(TradeCase, case_id)
        shipment = self.session.scalar(select(Shipment).where(Shipment.case_id == case_id))
        if shipment is None or trade_case is None:
            raise KeyError(case_id)
        if shipment.etd is None:
            raise ValueError("A Booking ETD is required for shipment delay scenarios")

        before_status = shipment.status
        with local_span(
            self.session,
            request_id,
            "workflow",
            "record_shipment_delay_scenarios",
            {
                "case_id": case_id,
                "transaction_id": trade_case.transaction_id,
                "reported_at": reported_at.isoformat(),
                "expected_delay_days": expected_delay_days,
            },
        ) as span:
            delays_key = ",".join(str(item) for item in expected_delay_days)
            event_key = f"{case_id}:{reported_at.isoformat()}:{delays_key}:SHIPMENT_DELAY"
            event_id = _stable_id("SEV", event_key)
            if self.session.get(ShipmentEvent, event_id) is None:
                self.session.add(
                    ShipmentEvent(
                        shipment_event_id=event_id,
                        shipment_id=shipment.shipment_id,
                        event_type="SHIPMENT_DELAY_REPORTED",
                        source_type="USER_REPORTED",
                        event_date=reported_at,
                        details_json={
                            "case_id": case_id,
                            "company_id": trade_case.company_id,
                            "transaction_id": trade_case.transaction_id,
                            "reported_at": reported_at.isoformat(),
                            "delay_days": expected_delay_days,
                            "planned_departure_date": shipment.etd.isoformat(),
                            "planned_departure_basis": "BOOKING_ETD",
                            "verified_actual": False,
                        },
                    )
                )

            scenarios = [
                {
                    "label": f"Revised expected departure +{days} days from Booking ETD",
                    "delay_days": days,
                    "revised_expected_departure_date": (
                        shipment.etd + timedelta(days=days)
                    ).isoformat(),
                    "shipment_timeline_basis": "BOOKING_ETD_PLUS_DELAY",
                    "conditional": True,
                    "verified_actual": False,
                    "source": "USER_REPORTED_SHIPMENT_DELAY",
                }
                for days in expected_delay_days
            ]
            calculation_id = _stable_id("SHIPCALC", event_key)
            basis_version = f"{trade_case.basis_version}:shipment-state-v{shipment.state_version}"
            calculation = self.session.get(CalculationResult, calculation_id)
            payload = {
                "event_source": "USER_REPORTED",
                "case_id": case_id,
                "company_id": trade_case.company_id,
                "transaction_id": trade_case.transaction_id,
                "shipment_id": shipment.shipment_id,
                "shipment_state_version": shipment.state_version,
                "reported_at": reported_at.isoformat(),
                "planned_departure_date": shipment.etd.isoformat(),
                "planned_departure_basis": "BOOKING_ETD",
                "shipment_status_before": before_status,
                "shipment_status_after": shipment.status,
                "scenarios": scenarios,
                "conditional_notice": (
                    "Booking ETD와 사용자 제보를 바탕으로 한 조건부 예상 출항일이며, "
                    "실제 On-board date나 B/L 발행일로 확정하지 않습니다."
                ),
            }
            if calculation is None:
                calculation = CalculationResult(
                    calculation_id=calculation_id,
                    case_id=case_id,
                    basis_version=basis_version,
                    scenario_name=SHIPMENT_DELAY_SCENARIO_NAME,
                    result_json=payload,
                    tool_version="shipment-delay-scenario.v18.0",
                    is_scenario=True,
                )
                self.session.add(calculation)
            else:
                calculation.basis_version = basis_version
                calculation.scenario_name = SHIPMENT_DELAY_SCENARIO_NAME
                calculation.result_json = payload
                calculation.tool_version = "shipment-delay-scenario.v18.0"

            result = ShipmentDelayScenarioResult(
                case_id=case_id,
                company_id=trade_case.company_id,
                transaction_id=trade_case.transaction_id,
                shipment_id=shipment.shipment_id,
                shipment_state_version=shipment.state_version,
                calculation_id=calculation_id,
                basis_version=basis_version,
                event_source="USER_REPORTED",
                reported_at=reported_at,
                planned_departure_date=shipment.etd,
                planned_departure_basis="BOOKING_ETD",
                shipment_status_before=before_status,
                shipment_status_after=shipment.status,
                scenarios=scenarios,
                conditional_notice=str(payload["conditional_notice"]),
            )
            span.update(
                {
                    "scenario_count": len(scenarios),
                    "event_source": result.event_source,
                    "financial_calculation_called": False,
                }
            )
            self.session.flush()
            return result
