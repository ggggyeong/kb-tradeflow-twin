from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Company,
    Document,
    FinancialEvent,
    PaymentObligation,
    Shipment,
    TradeCase,
    TransactionFinancialEventLink,
)
from app.domain_inputs.loaders.financial_calendar import load_financial_calendar_contract
from app.schemas.field_contract import DocumentType
from app.schemas.financial_calendar import FinancialCalendarContract


class FinancialCalendarService:
    """Own the minimal two-sheet financial-calendar ingestion boundary.

    The workbook never creates companies or trade cases.  Those identities are
    established by the authenticated session and the document-ingestion flow.
    This service only validates those existing identities and persists company
    financial events plus their transaction links.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def resolve_workbook_path(
        *,
        path: Path | None = None,
        batch_id: str | None = None,
    ) -> Path:
        """Resolve exactly one uploaded XLSX or a workspace-contained path."""
        if path is not None and batch_id is not None:
            raise ValueError("Provide either workbook_path or batch_id, not both")
        if batch_id is not None:
            from app.services.ingestion.batch_service import uploaded_batch_paths

            candidates = uploaded_batch_paths(batch_id)
            if len(candidates) != 1 or candidates[0].suffix.lower() != ".xlsx":
                raise ValueError(
                    f"Financial calendar batch {batch_id!r} must contain exactly one XLSX"
                )
            return candidates[0]
        if path is None:
            raise ValueError("Provide workbook_path or batch_id for the uploaded two-sheet XLSX")
        resolved = path.resolve()
        project_root = PROJECT_ROOT.resolve()
        if not resolved.is_relative_to(project_root):
            raise ValueError("workbook_path must remain inside the project workspace")
        if resolved.suffix.lower() != ".xlsx" or not resolved.is_file():
            raise ValueError("workbook_path must identify an existing .xlsx file")
        return resolved

    @staticmethod
    def load_contract(
        path: Path,
        *,
        company_id: str,
    ) -> FinancialCalendarContract:
        return load_financial_calendar_contract(path, company_id=company_id)

    def _resolve_trade_cases(
        self,
        contract: FinancialCalendarContract,
    ) -> dict[str, TradeCase]:
        company = self.session.get(Company, contract.company_id)
        if company is None:
            raise ValueError(
                f"Authenticated company {contract.company_id!r} does not exist; "
                "register trade documents first"
            )

        transaction_cases: dict[str, TradeCase] = {}
        for transaction_id in sorted({row.transaction_id for row in contract.links}):
            trade_case = self.session.scalar(
                select(TradeCase).where(
                    TradeCase.company_id == contract.company_id,
                    TradeCase.transaction_id == transaction_id,
                )
            )
            if trade_case is None:
                foreign_case = self.session.scalar(
                    select(TradeCase).where(TradeCase.transaction_id == transaction_id)
                )
                if foreign_case is not None:
                    raise ValueError(
                        f"Cross-company transaction reference denied: {transaction_id}"
                    )
                raise ValueError(
                    f"Unknown transaction_id {transaction_id!r} for company "
                    f"{contract.company_id!r}; register trade documents first"
                )
            transaction_cases[transaction_id] = trade_case
        return transaction_cases

    def _validate_event_ownership(self, contract: FinancialCalendarContract) -> None:
        for row in contract.events:
            existing = self.session.get(FinancialEvent, row.event_id)
            if existing is not None and existing.company_id != contract.company_id:
                raise ValueError(f"Cross-company event_id collision: {row.event_id}")

    def validate_workbook(
        self,
        path: Path,
        *,
        company_id: str,
    ) -> dict[str, Any]:
        """Validate workbook structure, values, links, and DB ownership read-only."""
        contract = self.load_contract(path, company_id=company_id)
        transaction_cases = self._resolve_trade_cases(contract)
        self._validate_event_ownership(contract)
        return {
            "valid": True,
            "source_path": contract.source_path,
            "source_sha256": contract.source_sha256,
            "contract_version": contract.version,
            "company_id": contract.company_id,
            "company_count": 1,
            "transaction_count": len(transaction_cases),
            "event_count": len(contract.events),
            "link_count": len(contract.links),
            "database_ownership_validated": True,
            "write_tables": [
                "financial_event",
                "transaction_financial_event_link",
            ],
            "calculation_owner": "financial_exposure",
        }

    def import_workbook(
        self,
        path: Path,
        *,
        company_id: str,
    ) -> dict[str, Any]:
        """Idempotently import event/link facts; never calculate financial risk."""
        return self.import_contract(self.load_contract(path, company_id=company_id))

    def import_contract(self, contract: FinancialCalendarContract) -> dict[str, Any]:
        """Persist only FinancialEvent and TransactionFinancialEventLink rows."""
        transaction_cases = self._resolve_trade_cases(contract)
        self._validate_event_ownership(contract)
        created = {"events": 0, "links": 0}

        for row in contract.events:
            event = self.session.get(FinancialEvent, row.event_id)
            if event is None:
                event = FinancialEvent(
                    financial_event_id=row.event_id,
                    company_id=contract.company_id,
                    source_ref_type="company_id",
                    source_ref_value=contract.company_id,
                    event_type=row.event_type_code,
                    event_date=row.event_date,
                    amount=row.amount,
                    currency=row.currency,
                )
                self.session.add(event)
                created["events"] += 1
            elif event.company_id != contract.company_id:
                raise ValueError(f"Cross-company event_id collision: {row.event_id}")

            event.case_id = None
            event.source_ref_type = "company_id"
            event.source_ref_value = contract.company_id
            event.event_type = row.event_type_code
            event.event_name = row.event_name
            event.event_date = row.event_date
            event.amount = row.amount
            event.currency = row.currency
            event.direction = "OUTFLOW"
            event.institution = row.financial_institution
            event.is_kb_contract = row.is_kb_contract
            event.event_status = "CONFIRMED"
            event.verified = True
            event.source_metadata_json = {
                "source_path": contract.source_path,
                "source_sha256": contract.source_sha256,
                "contract_version": contract.version,
                "company_context": contract.company_id,
            }
            event.description = row.event_name

        self.session.flush()
        for row in contract.links:
            trade_case = transaction_cases[row.transaction_id]
            link = self.session.scalar(
                select(TransactionFinancialEventLink).where(
                    TransactionFinancialEventLink.company_id == contract.company_id,
                    TransactionFinancialEventLink.transaction_id == row.transaction_id,
                    TransactionFinancialEventLink.financial_event_id == row.event_id,
                )
            )
            if link is None:
                colliding_link = self.session.get(
                    TransactionFinancialEventLink,
                    row.link_id,
                )
                if colliding_link is not None:
                    raise ValueError(f"Immutable link identity collision: {row.link_id}")
                link = TransactionFinancialEventLink(
                    transaction_event_link_id=row.link_id,
                    company_id=contract.company_id,
                    case_id=trade_case.case_id,
                    transaction_id=row.transaction_id,
                    financial_event_id=row.event_id,
                    link_type=row.link_type,
                    link_status=row.link_status,
                    dependency_scope="UNKNOWN",
                )
                self.session.add(link)
                created["links"] += 1

            # Idempotent re-import refreshes source facts but deliberately keeps
            # dependency_scope/linked_amount/currency added by later Human review.
            link.case_id = trade_case.case_id
            link.link_type = row.link_type
            link.link_status = row.link_status
            link.source_metadata_json = {
                **dict(link.source_metadata_json or {}),
                "source_path": contract.source_path,
                "source_sha256": contract.source_sha256,
                "contract_version": contract.version,
                "company_context": contract.company_id,
            }

        self.session.flush()
        return {
            "valid": True,
            "source_path": contract.source_path,
            "source_sha256": contract.source_sha256,
            "contract_version": contract.version,
            "company_id": contract.company_id,
            "company_count": 1,
            "transaction_count": len(transaction_cases),
            "event_count": len(contract.events),
            "link_count": len(contract.links),
            "calculation_owner": "financial_exposure",
            "created": created,
            "deduplicated": {
                "events": len(contract.events) - created["events"],
                "links": len(contract.links) - created["links"],
            },
            "preserved_tables": [
                "company",
                "trade_case",
                "shipment",
                "payment_obligation",
                "financial_transaction_timeline",
            ],
        }

    def update_financial_event_link_details(
        self,
        *,
        company_id: str,
        case_id: str,
        transaction_id: str,
        transaction_event_link_id: str,
        details: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Apply only explicit Human-reviewed optional link facts.

        The minimal two-sheet workbook intentionally omits these fields.  This
        method is the narrow post-conflict boundary that can add them without
        changing workbook-owned link status or any document/shipment facts.
        """
        normalized_company = company_id.strip()
        normalized_case = case_id.strip()
        normalized_transaction = transaction_id.strip()
        normalized_link_id = transaction_event_link_id.strip()
        normalized_actor = actor.strip()
        if not all(
            (
                normalized_company,
                normalized_case,
                normalized_transaction,
                normalized_link_id,
                normalized_actor,
            )
        ):
            raise ValueError("company_id, case_id, transaction_id, link id, and actor are required")
        if not isinstance(details, dict) or not details:
            raise ValueError("At least one reviewed financial-link detail is required")
        allowed_fields = {"dependency_scope", "linked_amount", "linked_currency"}
        unknown_fields = set(details) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported financial-link detail fields: {sorted(unknown_fields)}")

        trade_case = self.session.get(TradeCase, normalized_case)
        if trade_case is None:
            raise KeyError(f"Trade Case not found: {normalized_case}")
        if (
            trade_case.company_id != normalized_company
            or trade_case.transaction_id != normalized_transaction
        ):
            raise ValueError("TradeCase company_id/transaction_id ownership mismatch")
        link = self.session.get(TransactionFinancialEventLink, normalized_link_id)
        if link is None:
            raise KeyError(f"Financial event link not found: {normalized_link_id}")
        if (
            link.company_id != normalized_company
            or link.case_id != normalized_case
            or link.transaction_id != normalized_transaction
        ):
            raise ValueError("Financial event link ownership mismatch")

        reviewed_fields: list[str] = []
        if "dependency_scope" in details:
            dependency_scope = str(details["dependency_scope"] or "").strip().upper()
            if dependency_scope not in {"FULL", "PARTIAL"}:
                raise ValueError("dependency_scope must be FULL or PARTIAL")
            link.dependency_scope = dependency_scope
            link.dependency_basis = "HUMAN_CONFIRMED"
            link.coverage_confirmed_by = normalized_actor
            reviewed_fields.append("dependency_scope")

        if "linked_amount" in details:
            raw_amount = details["linked_amount"]
            if isinstance(raw_amount, bool):
                raise ValueError("linked_amount must be a positive number")
            try:
                linked_amount = float(raw_amount)
            except (TypeError, ValueError) as exc:
                raise ValueError("linked_amount must be a positive number") from exc
            if linked_amount <= 0:
                raise ValueError("linked_amount must be a positive number")
            link.linked_amount = linked_amount
            reviewed_fields.append("linked_amount")

        if "linked_currency" in details:
            linked_currency = str(details["linked_currency"] or "").strip().upper()
            if len(linked_currency) != 3 or not linked_currency.isalpha():
                raise ValueError("linked_currency must be a three-letter currency code")
            link.linked_currency = linked_currency
            reviewed_fields.append("linked_currency")

        link.confirmed_by = normalized_actor
        metadata = dict(link.source_metadata_json or {})
        metadata["human_link_review"] = {
            "actor": normalized_actor,
            "reviewed_fields": reviewed_fields,
        }
        link.source_metadata_json = metadata
        self.session.flush()
        missing_fields = (
            []
            if link.dependency_scope == "FULL"
            else [
                field
                for field, value in (
                    ("linked_amount", link.linked_amount),
                    ("linked_currency", link.linked_currency),
                )
                if value is None
            ]
            if link.dependency_scope == "PARTIAL"
            else ["dependency_scope"]
        )
        return {
            "transaction_event_link_id": link.transaction_event_link_id,
            "financial_event_id": link.financial_event_id,
            "company_id": link.company_id,
            "case_id": link.case_id,
            "transaction_id": link.transaction_id,
            "dependency_scope": link.dependency_scope,
            "linked_amount": link.linked_amount,
            "linked_currency": link.linked_currency,
            "reviewed_fields": reviewed_fields,
            "missing_fields": missing_fields,
            "complete": not missing_fields,
            "source": "HUMAN_CONFIRMED",
        }

    def select_monitoring_candidates(
        self,
        company_id: str,
        as_of_date: date,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Select only the authenticated company's risk-analysis candidates."""
        if not company_id.strip():
            raise ValueError("company_id is required")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if self.session.get(Company, company_id) is None:
            raise ValueError(f"Unknown company_id: {company_id}")

        has_financial_link = exists(
            select(TransactionFinancialEventLink.transaction_event_link_id).where(
                TransactionFinancialEventLink.case_id == TradeCase.case_id,
                TransactionFinancialEventLink.company_id == company_id,
            )
        )
        has_booking_document = exists(
            select(Document.document_id).where(
                Document.case_id == TradeCase.case_id,
                Document.doc_type == DocumentType.BOOKING_CONFIRMATION.value,
            )
        )
        has_invoice_document = exists(
            select(Document.document_id).where(
                Document.case_id == TradeCase.case_id,
                Document.doc_type == DocumentType.COMMERCIAL_INVOICE.value,
            )
        )
        has_bill_of_lading_document = exists(
            select(Document.document_id).where(
                Document.case_id == TradeCase.case_id,
                Document.doc_type == DocumentType.BILL_OF_LADING.value,
            )
        )
        has_planned_etd_without_bl_evidence = exists(
            select(Shipment.shipment_id).where(
                Shipment.case_id == TradeCase.case_id,
                Shipment.status == "AWAITING_BILL_OF_LADING",
                Shipment.etd.is_not(None),
                Shipment.bl_no.is_(None),
                Shipment.on_board_date.is_(None),
            )
        )
        # A missing B/L is itself the unresolved anchor being monitored.  The
        # deterministic tenor/day type are enough to calculate its safe frontier;
        # the final payment-date gate may still remain AFTER_CONFIRMATION.
        has_usable_payment_terms = exists(
            select(PaymentObligation.obligation_id).where(
                PaymentObligation.case_id == TradeCase.case_id,
                PaymentObligation.calculation_allowed.in_({"YES", "AFTER_CONFIRMATION"}),
                PaymentObligation.tenor_days.is_not(None),
                PaymentObligation.day_type_effective.in_({"CALENDAR", "BUSINESS"}),
            )
        )
        pre_bl_monitoring_eligible = and_(
            TradeCase.status == "AWAITING_DOCUMENT",
            has_booking_document,
            has_invoice_document,
            ~has_bill_of_lading_document,
            has_planned_etd_without_bl_evidence,
            has_usable_payment_terms,
        )
        cases = list(
            self.session.scalars(
                select(TradeCase)
                .where(
                    TradeCase.company_id == company_id,
                    or_(
                        TradeCase.monitoring_enabled.is_(True),
                        pre_bl_monitoring_eligible,
                    ),
                    TradeCase.transaction_id.is_not(None),
                    has_financial_link,
                )
                .order_by(TradeCase.case_id)
                .limit(limit)
            )
        )
        return {
            "company_id": company_id,
            "as_of_date": as_of_date.isoformat(),
            "candidate_count": len(cases),
            "candidates": [
                {
                    "case_id": trade_case.case_id,
                    "company_id": trade_case.company_id,
                    "transaction_id": trade_case.transaction_id,
                    "basis_version": trade_case.basis_version,
                }
                for trade_case in cases
            ],
            "calculation_owner": "financial_exposure",
        }
