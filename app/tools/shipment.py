from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Shipment, TradeCase
from app.db.session import session_scope


def build_shipment_snapshot(session: Session, case_id: str) -> dict[str, Any]:
    """Build the safe lifecycle/physical-state view for one internal TradeCase ID."""
    trade_case = session.get(TradeCase, case_id)
    if trade_case is None:
        raise KeyError(f"Trade Case not found: {case_id}")
    shipment = session.scalar(select(Shipment).where(Shipment.case_id == case_id))
    identity = {
        "case_id": case_id,
        "company_id": trade_case.company_id,
        "transaction_id": trade_case.transaction_id,
    }
    if shipment is None:
        return {
            **identity,
            "status": "UNKNOWN",
            "stored_status": None,
            "departure_status": "UNKNOWN",
            "shipment_present": False,
            "planned": None,
            "actual": None,
            "route": None,
            "route_source": None,
            "unknowns": ["shipment_record"],
            "evidence": [f"trade_case:{case_id}"],
        }

    route_present = bool(shipment.port_of_loading or shipment.port_of_discharge)
    if not route_present:
        route_source = None
    elif shipment.booking_no:
        # Booking ports are projected first and remain the shared route when a
        # later B/L arrives. The minimal Shipment schema intentionally has one route.
        route_source = "BOOKING_CONFIRMATION"
    elif shipment.bl_no or shipment.actual_vessel_name or shipment.on_board_date:
        route_source = "BILL_OF_LADING"
    else:
        route_source = "UNKNOWN"

    unknowns = [
        field
        for field, value in {
            "booking_no": shipment.booking_no,
            "etd": shipment.etd,
            "bl_no": shipment.bl_no,
            "on_board_date": shipment.on_board_date,
        }.items()
        if value is None
    ]
    departure_status = "CONFIRMED_DEPARTED" if shipment.on_board_date is not None else "UNKNOWN"
    planned = {
        "booking_no": shipment.booking_no,
        "vessel_name": shipment.planned_vessel_name,
        "voyage_no": shipment.planned_voyage_no,
        "etd": shipment.etd,
    }
    actual = {
        "bl_no": shipment.bl_no,
        "vessel_name": shipment.actual_vessel_name,
        "voyage_no": shipment.actual_voyage_no,
        "on_board_date": shipment.on_board_date,
    }
    route = {
        "port_of_loading": shipment.port_of_loading,
        "port_of_discharge": shipment.port_of_discharge,
    }
    return {
        **identity,
        "shipment_id": shipment.shipment_id,
        "shipment_present": True,
        # This is a document/lifecycle state, not a claim that the vessel departed.
        "status": shipment.status,
        "stored_status": shipment.status,
        "departure_status": departure_status,
        "state_version": shipment.state_version,
        "booking_no": shipment.booking_no,
        "bl_no": shipment.bl_no,
        "planned_vessel_name": shipment.planned_vessel_name,
        "planned_voyage_no": shipment.planned_voyage_no,
        "actual_vessel_name": shipment.actual_vessel_name,
        "actual_voyage_no": shipment.actual_voyage_no,
        "port_of_loading": shipment.port_of_loading,
        "port_of_discharge": shipment.port_of_discharge,
        "etd": shipment.etd,
        "on_board_date": shipment.on_board_date,
        "planned": planned,
        "actual": actual,
        "route": route,
        "route_source": route_source,
        "next_check_at": shipment.next_check_at,
        "unknowns": unknowns,
        "evidence": [
            f"trade_case:{case_id}",
            f"transaction:{trade_case.transaction_id or 'UNKNOWN'}",
            f"shipment:{shipment.shipment_id}:state_version:{shipment.state_version}",
        ],
    }


@tool
def read_shipment_snapshot(case_id: str) -> dict[str, Any]:
    """Read Shipment by case_id, preserving lifecycle state and safe departure unknowns."""
    with session_scope() as session:
        return build_shipment_snapshot(session, case_id)
