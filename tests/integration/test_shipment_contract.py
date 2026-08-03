from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Base,
    CalculationResult,
    Shipment,
    ShipmentEvent,
    TradeCase,
)
from app.services.advisory import SHIPMENT_DELAY_SCENARIO_NAME, AdvisoryService
from app.services.ingestion.batch_service import BatchService
from app.tools.shipment import build_shipment_snapshot
from scripts.generate_live_trade_documents import FIXTURES, _bill_of_lading

FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"


def test_invoice_only_case_has_no_fabricated_shipment_projection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    invoice = FIXTURE_DIR / "02_TRD-001_invoice.pdf"

    with Session(engine) as session:
        service = BatchService(session)
        service.analyze([invoice], "SHIPMENT-INVOICE-ONLY")
        service.commit("SHIPMENT-INVOICE-ONLY")

        trade_case = session.get(TradeCase, "TRD-001")
        assert trade_case is not None
        assert trade_case.transaction_id == "TXN001"
        assert session.scalar(select(Shipment).where(Shipment.case_id == "TRD-001")) is None
        snapshot = build_shipment_snapshot(session, "TRD-001")
        assert snapshot["case_id"] == "TRD-001"
        assert snapshot["transaction_id"] == "TXN001"
        assert snapshot["shipment_present"] is False
        assert snapshot["status"] == "UNKNOWN"


def test_snapshot_preserves_lifecycle_state_and_separates_departure_confirmation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    paths = sorted(FIXTURE_DIR.glob("*TRD-003*.pdf"))

    with Session(engine) as session:
        service = BatchService(session)
        service.analyze(paths, "SHIPMENT-SNAPSHOT")
        service.commit("SHIPMENT-SNAPSHOT")

        snapshot = build_shipment_snapshot(session, "TRD-003")
        assert snapshot["company_id"] == "A"
        assert snapshot["transaction_id"] == "TXN003"
        assert snapshot["status"] == "AWAITING_BILL_OF_LADING"
        assert snapshot["departure_status"] == "UNKNOWN"
        assert snapshot["planned"] == {
            "booking_no": "BK-TRD-003",
            "vessel_name": "KB SAKURA",
            "voyage_no": "303E",
            "etd": date(2026, 8, 15),
        }
        assert snapshot["actual"] == {
            "bl_no": None,
            "vessel_name": None,
            "voyage_no": None,
            "on_board_date": None,
        }
        assert snapshot["route"] == {
            "port_of_loading": "BUSAN",
            "port_of_discharge": "YOKOHAMA",
        }
        assert snapshot["route_source"] == "BOOKING_CONFIRMATION"


def test_scenario_is_etd_grounded_idempotent_and_does_not_mutate_shipment_state() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    paths = sorted(FIXTURE_DIR.glob("*TRD-003*.pdf"))

    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(paths, "SHIPMENT-SCENARIO")
        batch.commit("SHIPMENT-SCENARIO")
        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert shipment is not None
        before = (
            shipment.status,
            shipment.etd,
            shipment.on_board_date,
            shipment.state_version,
        )

        service = AdvisoryService(session)
        first = service.record_shipment_delay_scenarios(
            "TRD-003",
            reported_at=date(2026, 8, 20),
            request_id="shipment-delay-first",
            expected_delay_days=[14, 7, 14],
        )
        duplicate = service.record_shipment_delay_scenarios(
            "TRD-003",
            reported_at=date(2026, 8, 20),
            request_id="shipment-delay-duplicate",
            expected_delay_days=[7, 14],
        )
        session.refresh(shipment)

        assert duplicate.calculation_id == first.calculation_id
        assert duplicate.shipment_state_version == first.shipment_state_version == 1
        assert (
            shipment.status,
            shipment.etd,
            shipment.on_board_date,
            shipment.state_version,
        ) == before
        assert first.planned_departure_date == date(2026, 8, 15)
        assert first.planned_departure_basis == "BOOKING_ETD"
        assert first.scenarios == [
            {
                "label": "Revised expected departure +7 days from Booking ETD",
                "delay_days": 7,
                "revised_expected_departure_date": "2026-08-22",
                "shipment_timeline_basis": "BOOKING_ETD_PLUS_DELAY",
                "conditional": True,
                "verified_actual": False,
                "source": "USER_REPORTED_SHIPMENT_DELAY",
            },
            {
                "label": "Revised expected departure +14 days from Booking ETD",
                "delay_days": 14,
                "revised_expected_departure_date": "2026-08-29",
                "shipment_timeline_basis": "BOOKING_ETD_PLUS_DELAY",
                "conditional": True,
                "verified_actual": False,
                "source": "USER_REPORTED_SHIPMENT_DELAY",
            },
        ]
        assert all(
            "hypothetical_anchor_date" not in scenario
            and "hypothetical_bl_issue_date" not in scenario
            and "expected_on_board_date" not in scenario
            for scenario in first.scenarios
        )
        calculation = session.get(CalculationResult, first.calculation_id)
        assert calculation is not None
        assert calculation.scenario_name == SHIPMENT_DELAY_SCENARIO_NAME
        assert calculation.result_json["transaction_id"] == "TXN003"
        assert calculation.result_json["planned_departure_date"] == "2026-08-15"
        assert session.scalar(select(func.count()).select_from(ShipmentEvent)) == 1


def test_actual_bl_projection_increments_state_version_once_and_preserves_booking_route(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    initial_paths = sorted(FIXTURE_DIR.glob("*TRD-003*.pdf"))

    with Session(engine) as session:
        service = BatchService(session)
        service.analyze(initial_paths, "SHIPMENT-ACTUAL-INITIAL")
        service.commit("SHIPMENT-ACTUAL-INITIAL")
        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert shipment is not None
        assert shipment.state_version == 1
        planned_route = (shipment.port_of_loading, shipment.port_of_discharge)

        fixture = next(item for item in FIXTURES if item.reference == "TRD-003")
        later_bl = tmp_path / "later-bl.pdf"
        _bill_of_lading(
            later_bl,
            replace(fixture, on_board_date=fixture.actual_anchor_date),
        )
        service.analyze([later_bl], "SHIPMENT-ACTUAL-BL")
        service.commit("SHIPMENT-ACTUAL-BL")
        session.refresh(shipment)

        assert shipment.status == "BL_RECEIVED"
        assert shipment.on_board_date == date(2026, 8, 24)
        assert shipment.state_version == 2
        assert (shipment.port_of_loading, shipment.port_of_discharge) == planned_route

        service.commit("SHIPMENT-ACTUAL-BL")
        session.refresh(shipment)
        assert shipment.state_version == 2
        snapshot = build_shipment_snapshot(session, "TRD-003")
        assert snapshot["departure_status"] == "CONFIRMED_DEPARTED"
        assert snapshot["route_source"] == "BOOKING_CONFIRMATION"
