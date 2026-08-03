from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.db.session import init_database, session_scope
from app.domain_inputs.loaders.financial_calendar import load_financial_calendar_contract
from app.schemas.financial_calendar import FinancialCalendarContract
from app.services.financial_calendar import FinancialCalendarService
from app.services.ingestion.batch_service import BatchService
from app.services.virtual_clock import set_date

DEMO_DIR = PROJECT_ROOT / "data" / "demo_judges_v2"
CALENDAR_PATH = DEMO_DIR / "KB_TradeFlow_금융일정_2Sheet_데모예시.xlsx"


def load_demo_financial_contract(
    calendar_path: Path = CALENDAR_PATH,
    company_id: str = "DEMO-A",
) -> FinancialCalendarContract:
    """Load the same exact two-sheet workbook used in the judge demonstration."""
    return load_financial_calendar_contract(
        calendar_path,
        company_id=company_id,
    )


def prepare_demo_database(
    *,
    include_documents: bool = True,
    include_financial_calendar: bool = True,
    set_demo_date: bool = True,
) -> dict[str, Any]:
    """Idempotently prepare the document and finance facts used by demos 2 and 3."""
    init_database()
    result: dict[str, Any] = {}
    if set_demo_date:
        result["virtual_date"] = set_date(date(2026, 7, 31)).isoformat()
    with session_scope() as session:
        if include_documents:
            service = BatchService(session)
            batch_id = "DEMO-DOCS-20260710"
            analysis = service.analyze(sorted(DEMO_DIR.glob("*.pdf")), batch_id)
            for request in analysis.summary.human_requests:
                if (
                    request.case_id == "TRD-DEMO-002"
                    and request.exact_standard_field == "on_board_date"
                ):
                    analysis = service.apply_document_field_override(
                        batch_id,
                        request.document_id,
                        request.exact_standard_field,
                        "Jun 28, 2026",
                        actor="judge-demo-preparation",
                        reason="Reviewed synthetic demo fact",
                    )
            commit = service.commit(batch_id)
            result["documents"] = {
                "batch_id": batch_id,
                "file_count": analysis.summary.file_count,
                "case_count": analysis.summary.case_count,
                "committed_case_ids": commit.committed_case_ids,
            }
        if include_financial_calendar:
            imported = FinancialCalendarService(session).import_contract(
                load_demo_financial_contract()
            )
            result["financial_calendar"] = imported
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents-only", action="store_true")
    parser.add_argument("--finance-only", action="store_true")
    args = parser.parse_args()
    if args.documents_only and args.finance_only:
        parser.error("choose at most one of --documents-only and --finance-only")
    result = prepare_demo_database(
        include_documents=not args.finance_only,
        include_financial_calendar=not args.documents_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
