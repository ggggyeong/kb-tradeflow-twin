from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import TradeCase
from app.db.session import init_database, session_scope
from app.schemas.financial_calendar import FinancialCalendarContract
from app.services.financial_calendar import FinancialCalendarService
from app.services.ingestion.batch_service import BatchService
from app.services.product_advisory import update_company_product_profile
from app.services.virtual_clock import set_date

DEMO_ROOT = PROJECT_ROOT / "data" / "judge_demo_final"
DEMO_DATE = date(2026, 8, 20)
PREPARATION_ACTOR = "final-demo-portfolio-preparation"


@dataclass(frozen=True)
class DemoSpec:
    demo_id: str
    directory_name: str
    batch_id: str
    has_financial_calendar: bool
    product_not_met_requirements: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        return DEMO_ROOT / self.directory_name

    @property
    def financial_seed_path(self) -> Path:
        return self.directory / "financial_calendar_seed.json"


DEMO_SPECS: dict[str, DemoSpec] = {
    "0": DemoSpec(
        demo_id="0",
        directory_name="demo_0_complete_batch",
        batch_id="FINAL-DEMO0-DOCS",
        has_financial_calendar=False,
    ),
    "1": DemoSpec(
        demo_id="1",
        directory_name="demo_1_manual_today_risk",
        batch_id="FINAL-DEMO1-DOCS",
        has_financial_calendar=True,
        product_not_met_requirements=(
            "KB_CREDIT_GRADE_REQUIREMENT",
            "STRATEGIC_TARGET_COMPANY",
            "KSURE_PROGRAM_ELIGIBILITY",
        ),
    ),
    "2": DemoSpec(
        demo_id="2",
        directory_name="demo_2_reported_delay",
        batch_id="FINAL-DEMO2-DOCS",
        has_financial_calendar=True,
        product_not_met_requirements=("KSURE_PROGRAM_ELIGIBILITY",),
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _document_paths(spec: DemoSpec) -> list[Path]:
    if not spec.directory.is_dir():
        raise FileNotFoundError(f"Demo asset directory not found: {spec.directory}")
    paths = sorted(spec.directory.glob("*.pdf"))
    if not paths:
        raise FileNotFoundError(f"No trade-document PDFs found in {spec.directory}")
    return paths


def _sheet_rows(payload: dict[str, Any], sheet_name: str) -> list[dict[str, Any]]:
    sheets = payload.get("sheets")
    if isinstance(sheets, list):
        matches = [sheet for sheet in sheets if sheet.get("sheet_name") == sheet_name]
        if len(matches) != 1 or not isinstance(matches[0].get("rows"), list):
            raise ValueError(f"Financial seed must contain exactly one {sheet_name!r} sheet")
        rows = matches[0]["rows"]
    elif isinstance(sheets, dict):
        matches = [
            sheet for key, sheet in sheets.items() if str(key).split(".", 1)[-1] == sheet_name
        ]
        if len(matches) != 1 or not isinstance(matches[0], dict):
            raise ValueError(f"Financial seed must contain exactly one {sheet_name!r} sheet")
        headers = matches[0].get("headers")
        raw_rows = matches[0].get("rows")
        if not isinstance(headers, list) or not isinstance(raw_rows, list):
            raise ValueError(f"Financial seed sheet {sheet_name!r} needs headers and rows")
        if any(not isinstance(row, list) or len(row) != len(headers) for row in raw_rows):
            raise ValueError(f"Financial seed sheet {sheet_name!r} row width is invalid")
        rows = [dict(zip(headers, row, strict=True)) for row in raw_rows]
    else:
        raise ValueError("financial_calendar_seed.json must contain sheets")
    if not rows:
        raise ValueError(f"Financial seed sheet {sheet_name!r} must not be empty")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Financial seed sheet {sheet_name!r} rows must be objects")
    return rows


def _normalize_financial_seed(path: Path) -> FinancialCalendarContract:
    """Convert the judge-friendly two-sheet JSON into the domain contract."""
    if not path.is_file():
        raise FileNotFoundError(f"Financial seed not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("financial_calendar_seed.json must contain a JSON object")

    # Accept the full Pydantic contract as well as the deliberately small
    # judge-facing two-sheet envelope generated for the final demonstration.
    if isinstance(payload.get("events"), list) and isinstance(payload.get("links"), list):
        contract_payload = dict(payload)
        contract_payload.setdefault("source_sha256", _sha256(path))
        contract_payload.setdefault("source_path", str(path.relative_to(PROJECT_ROOT)))
        contract_payload.setdefault("version", "demo-financial-calendar-minimal-v1")
        return FinancialCalendarContract.model_validate(contract_payload)

    events = _sheet_rows(payload, "금융이벤트")
    links = _sheet_rows(payload, "거래연결")
    declared_company_id = str(
        payload.get("company_id_from_authenticated_session") or payload.get("company_id") or ""
    ).strip()
    company_ids = {
        str(row.get("company_id") or declared_company_id).strip() for row in [*events, *links]
    }
    if "" in company_ids or len(company_ids) != 1:
        raise ValueError("Every financial event and link must use one identical company_id")
    company_id = next(iter(company_ids))
    event_ids = {str(row.get("event_id") or "").strip() for row in events}
    if "" in event_ids or len(event_ids) != len(events):
        raise ValueError("Financial event_id values must be non-empty and unique")

    normalized_events: list[dict[str, Any]] = []
    for row in events:
        event_type = str(row.get("event_type_code") or "").strip()
        institution = str(row.get("financial_institution") or "").strip()
        normalized_events.append(
            {
                "event_id": str(row["event_id"]).strip(),
                "event_type_code": event_type,
                "event_name": str(row.get("event_name") or event_type).strip(),
                "event_date": row.get("event_date"),
                "amount": row.get("amount"),
                "currency": str(row.get("currency") or "").strip().upper(),
                "financial_institution": institution,
                "is_kb_contract": bool(
                    row.get("is_kb_contract")
                    if "is_kb_contract" in row
                    else institution.upper().startswith("KB") or "국민" in institution
                ),
            }
        )

    normalized_links: list[dict[str, Any]] = []
    for row in links:
        event_id = str(row.get("event_id") or "").strip()
        if event_id not in event_ids:
            raise ValueError(f"Financial link references unknown event_id: {event_id}")
        transaction_id = str(row.get("transaction_id") or "").strip()
        if not transaction_id:
            raise ValueError("Every financial link must have a transaction_id")
        stable_key = f"{company_id}:{transaction_id}:{event_id}"
        normalized_links.append(
            {
                "link_id": str(row.get("link_id") or "").strip()
                or f"LINK-{hashlib.sha256(stable_key.encode()).hexdigest()[:16].upper()}",
                "transaction_id": transaction_id,
                "event_id": event_id,
                "link_type": str(row.get("link_type") or "").strip(),
                "link_status": str(row.get("link_status") or "").strip(),
                "dependency_scope": str(row.get("dependency_scope") or "UNKNOWN").strip(),
            }
        )

    return FinancialCalendarContract.model_validate(
        {
            "source_sha256": _sha256(path),
            "source_path": str(path.relative_to(PROJECT_ROOT)),
            "version": str(payload.get("contract_version") or "demo-financial-calendar-minimal-v1"),
            "company_id": company_id,
            "events": normalized_events,
            "links": normalized_links,
        }
    )


def _case_rows(session: Session, case_ids: Iterable[str]) -> list[TradeCase]:
    rows: list[TradeCase] = []
    for case_id in case_ids:
        trade_case = session.get(TradeCase, case_id)
        if trade_case is None:
            raise RuntimeError(f"Committed TradeCase was not found: {case_id}")
        rows.append(trade_case)
    return rows


def _prepare_product_profile(
    session: Session,
    *,
    spec: DemoSpec,
    cases: list[TradeCase],
) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for company_id in sorted({str(item.company_id) for item in cases if item.company_id}):
        profiles[company_id] = update_company_product_profile(
            session,
            company_id=company_id,
            borrower_type="CORPORATION",
            actor=PREPARATION_ACTOR,
        )
    for trade_case in cases:
        if not trade_case.company_id:
            continue
        for requirement_code in spec.product_not_met_requirements:
            profiles[trade_case.company_id] = update_company_product_profile(
                session,
                company_id=trade_case.company_id,
                case_id=trade_case.case_id,
                requirement_code=requirement_code,
                requirement_satisfied=False,
                actor=PREPARATION_ACTOR,
            )
    return profiles


def _prepare_one(session: Session, spec: DemoSpec) -> dict[str, Any]:
    paths = _document_paths(spec)
    service = BatchService(session)
    analysis = service.analyze(paths, spec.batch_id)
    if analysis.summary.unmatched_document_ids:
        raise ValueError(
            f"Demo {spec.demo_id} has unmatched documents: "
            f"{analysis.summary.unmatched_document_ids}"
        )
    if analysis.summary.type_confirmation_document_ids:
        raise ValueError(
            f"Demo {spec.demo_id} has unclassified documents: "
            f"{analysis.summary.type_confirmation_document_ids}"
        )
    if analysis.summary.human_requests:
        fields = sorted({request.field_path for request in analysis.summary.human_requests})
        raise ValueError(
            f"Demo {spec.demo_id} setup contains unresolved core fields: {fields}. "
            "Preparation never fabricates Human answers."
        )
    commit = service.commit(spec.batch_id)
    if commit.pending_case_ids:
        raise ValueError(
            f"Demo {spec.demo_id} contains confirmation-required cases: {commit.pending_case_ids}"
        )
    cases = _case_rows(session, commit.committed_case_ids)
    transactions = {str(item.transaction_id) for item in cases if item.transaction_id}

    financial_result: dict[str, Any] | None = None
    if spec.has_financial_calendar:
        contract = _normalize_financial_seed(spec.financial_seed_path)
        if contract.company_id not in {str(item.company_id) for item in cases}:
            raise ValueError(
                f"Financial company_id {contract.company_id!r} does not match Demo "
                f"{spec.demo_id} document company"
            )
        linked_transactions = {row.transaction_id for row in contract.links}
        unknown_transactions = sorted(linked_transactions - transactions)
        if unknown_transactions:
            raise ValueError(
                f"Financial seed references unknown transactions: {unknown_transactions}"
            )
        financial_result = FinancialCalendarService(session).import_contract(contract)

    profiles = _prepare_product_profile(session, spec=spec, cases=cases)
    proposals_by_case_id = {proposal.case_id: proposal for proposal in analysis.summary.cases}
    return {
        "demo_id": spec.demo_id,
        "asset_directory": str(spec.directory.relative_to(PROJECT_ROOT)),
        "batch_id": spec.batch_id,
        "document_file_count": len(paths),
        "case_count": analysis.summary.case_count,
        "committed_case_ids": commit.committed_case_ids,
        "transactions": sorted(transactions),
        "missing_documents": {
            case.case_id: [
                item.value for item in proposals_by_case_id[case.case_id].missing_documents
            ]
            for case in cases
            if proposals_by_case_id[case.case_id].missing_documents
        },
        "documents_created": commit.created_document_count,
        "documents_deduplicated": commit.duplicate_document_count,
        "financial_calendar": financial_result,
        "product_profile_companies": sorted(profiles),
    }


def selected_demo_ids(value: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(DEMO_SPECS)
    if value not in DEMO_SPECS:
        raise ValueError(f"Unsupported demo selector: {value}")
    return (value,)


def prepare_final_demo_portfolio(demo_ids: Iterable[str]) -> dict[str, Any]:
    """Prepare selected final demos without resetting data or calling a model API."""
    normalized_ids = tuple(demo_ids)
    unknown = sorted(set(normalized_ids) - set(DEMO_SPECS))
    if unknown:
        raise ValueError(f"Unsupported demo ids: {unknown}")
    init_database()
    virtual_date = set_date(DEMO_DATE)
    with session_scope() as session:
        demos = [_prepare_one(session, DEMO_SPECS[demo_id]) for demo_id in normalized_ids]
    return {
        "status": "PREPARED",
        "virtual_date": virtual_date.isoformat(),
        "database_reset": False,
        "model_api_calls": 0,
        "demos": demos,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently prepare the final KB TradeFlow judge-demo portfolio."
    )
    parser.add_argument("--demo", choices=("all", "0", "1", "2"), default="all")
    args = parser.parse_args()
    result = prepare_final_demo_portfolio(selected_demo_ids(args.demo))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
