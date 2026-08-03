from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative metadata base."""


class TimestampMixin:
    """UTC creation timestamp shared by audit tables."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IngestionBatch(Base, TimestampMixin):
    __tablename__ = "ingestion_batch"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="STAGED")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    analysis_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Document(Base, TimestampMixin):
    __tablename__ = "document"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batch.batch_id"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("trade_case.case_id"))
    doc_type: Mapped[str] = mapped_column(String(40))
    template_id: Mapped[str] = mapped_column(String(60))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    object_path: Mapped[str] = mapped_column(Text)
    classification_status: Mapped[str] = mapped_column(String(32))
    source_references_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    facts: Mapped[list[DocumentFact]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentFact(Base, TimestampMixin):
    __tablename__ = "document_fact"
    __table_args__ = (
        UniqueConstraint("document_id", "exact_standard_field", name="uq_document_exact_field"),
    )

    fact_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.document_id"))
    exact_standard_field: Mapped[str] = mapped_column(String(120))
    raw_value: Mapped[str] = mapped_column(Text)
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_source: Mapped[str] = mapped_column(String(40), default="DOCUMENT")

    document: Mapped[Document] = relationship(back_populates="facts")


class DocumentFieldOverride(Base, TimestampMixin):
    """Append-only human value layered over an immutable extracted document fact."""

    __tablename__ = "document_field_override"

    override_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.document_id"), index=True)
    exact_standard_field: Mapped[str] = mapped_column(String(120), index=True)
    raw_input: Mapped[str] = mapped_column(Text)
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_normalized_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_source: Mapped[str] = mapped_column(String(40), default="MANUAL_OVERRIDE")


class Company(Base, TimestampMixin):
    """Company-level owner of transactions and reusable financial events."""

    __tablename__ = "company"

    company_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200), index=True)
    # Narrow customer-profile facts used only for product hard filters.  This
    # keeps the domain model compact while allowing explicit answers to be
    # reused across Chat threads.
    product_advisory_profile_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
    )


class TradeCase(Base, TimestampMixin):
    __tablename__ = "trade_case"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "transaction_id",
            name="uq_trade_case_company_transaction",
        ),
    )

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(120), index=True)
    company: Mapped[str] = mapped_column(String(200))
    counterparty: Mapped[str] = mapped_column(String(200))
    invoice_no: Mapped[str | None] = mapped_column(String(120), index=True)
    currency: Mapped[str | None] = mapped_column(String(12))
    goods: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT")
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    basis_version: Mapped[str] = mapped_column(String(80), default="basis-1")

    shipment: Mapped[Shipment | None] = relationship(
        back_populates="trade_case", uselist=False, cascade="all, delete-orphan"
    )
    obligations: Mapped[list[PaymentObligation]] = relationship(
        back_populates="trade_case", cascade="all, delete-orphan"
    )


class Shipment(Base, TimestampMixin):
    __tablename__ = "shipment"

    shipment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("trade_case.case_id"), unique=True)
    booking_no: Mapped[str | None] = mapped_column(String(120), index=True)
    bl_no: Mapped[str | None] = mapped_column(String(120), index=True)
    planned_vessel_name: Mapped[str | None] = mapped_column(String(200))
    planned_voyage_no: Mapped[str | None] = mapped_column(String(80))
    actual_vessel_name: Mapped[str | None] = mapped_column(String(200))
    actual_voyage_no: Mapped[str | None] = mapped_column(String(80))
    port_of_loading: Mapped[str | None] = mapped_column(String(200))
    port_of_discharge: Mapped[str | None] = mapped_column(String(200))
    etd: Mapped[date | None] = mapped_column(Date)
    on_board_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40), default="PLANNED")
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    next_check_at: Mapped[date | None] = mapped_column(Date)

    trade_case: Mapped[TradeCase] = relationship(back_populates="shipment")


class PaymentObligation(Base, TimestampMixin):
    __tablename__ = "payment_obligation"

    obligation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("trade_case.case_id"))
    tranche_index: Mapped[int] = mapped_column(Integer, default=1)
    raw_text: Mapped[str] = mapped_column(Text)
    payment_method: Mapped[str] = mapped_column(String(40))
    credit_term_type: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    lc_type: Mapped[str | None] = mapped_column(String(40))
    anchor_type_extracted: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    anchor_type_effective: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    tenor_days: Mapped[int | None] = mapped_column(Integer)
    day_type_extracted: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    day_type_effective: Mapped[str | None] = mapped_column(String(40))
    payment_ratio: Mapped[str | None] = mapped_column(String(40))
    advance_or_deferred: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    calculation_allowed: Mapped[str] = mapped_column(String(40))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_source: Mapped[str] = mapped_column(String(60), default="UNVERIFIED")
    verification_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    trade_case: Mapped[TradeCase] = relationship(back_populates="obligations")


class FinancialTransactionTimeline(Base, TimestampMixin):
    """Imported reference timeline; never overwrites live shipment/document facts."""

    __tablename__ = "financial_transaction_timeline"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "transaction_id",
            "source_sha256",
            name="uq_financial_timeline_source",
        ),
    )

    timeline_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("trade_case.case_id"), index=True)
    transaction_id: Mapped[str] = mapped_column(String(120), index=True)
    invoice_amount: Mapped[float] = mapped_column(Float)
    invoice_currency: Mapped[str] = mapped_column(String(12))
    payment_terms_raw: Mapped[str] = mapped_column(Text)
    anchor_type_extracted: Mapped[str] = mapped_column(String(40))
    anchor_type_effective: Mapped[str] = mapped_column(String(40))
    anchor_value_source: Mapped[str] = mapped_column(String(40))
    tenor_days: Mapped[int | None] = mapped_column(Integer)
    day_type_effective: Mapped[str | None] = mapped_column(String(40))
    planned_anchor_date: Mapped[date] = mapped_column(Date)
    reviewed_actual_anchor_date: Mapped[date] = mapped_column(Date)
    delay_days: Mapped[int] = mapped_column(Integer)
    planned_payment_due_date: Mapped[date | None] = mapped_column(Date)
    revised_payment_due_date: Mapped[date | None] = mapped_column(Date)
    calculation_status: Mapped[str] = mapped_column(String(24))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FinancialEvent(Base, TimestampMixin):
    """Company-owned event that may link to zero, one, or many transactions."""

    __tablename__ = "financial_event"

    financial_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("trade_case.case_id"))
    source_ref_type: Mapped[str] = mapped_column(String(40))
    source_ref_value: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(60))
    event_name: Mapped[str] = mapped_column(Text, default="")
    event_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(12))
    direction: Mapped[str] = mapped_column(String(16), default="OUTFLOW")
    event_counterparty: Mapped[str | None] = mapped_column(String(200))
    institution: Mapped[str | None] = mapped_column(String(200))
    is_kb_contract: Mapped[bool | None] = mapped_column(Boolean)
    facility_id: Mapped[str | None] = mapped_column(String(120))
    event_status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    source_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")


class TransactionFinancialEventLink(Base, TimestampMixin):
    """Explicit relationship and dependency facts for a transaction/event pair."""

    __tablename__ = "transaction_financial_event_link"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "transaction_id",
            "financial_event_id",
            name="uq_transaction_financial_event",
        ),
    )

    transaction_event_link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("trade_case.case_id"), index=True)
    transaction_id: Mapped[str] = mapped_column(String(120), index=True)
    financial_event_id: Mapped[str] = mapped_column(
        ForeignKey("financial_event.financial_event_id"), index=True
    )
    link_type: Mapped[str] = mapped_column(String(60))
    link_status: Mapped[str] = mapped_column(String(24))
    dependency_scope: Mapped[str | None] = mapped_column(String(24))
    dependency_basis: Mapped[str | None] = mapped_column(String(40))
    coverage_confirmed_by: Mapped[str | None] = mapped_column(String(40))
    linked_amount: Mapped[float | None] = mapped_column(Float)
    linked_currency: Mapped[str | None] = mapped_column(String(12))
    confirmed_by: Mapped[str | None] = mapped_column(String(120))
    adjustability: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    alternative_funds_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    supplier_criticality: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    disruption_risk: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    notes: Mapped[str | None] = mapped_column(Text)
    source_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ShipmentEvent(Base, TimestampMixin):
    __tablename__ = "shipment_event"

    shipment_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipment.shipment_id"))
    event_type: Mapped[str] = mapped_column(String(80))
    source_type: Mapped[str] = mapped_column(String(40))
    event_date: Mapped[date] = mapped_column(Date)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CalculationResult(Base, TimestampMixin):
    __tablename__ = "calculation_result"

    calculation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("trade_case.case_id"))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"))
    transaction_id: Mapped[str | None] = mapped_column(String(120), index=True)
    basis_version: Mapped[str] = mapped_column(String(80))
    scenario_name: Mapped[str] = mapped_column(String(120))
    as_of_date: Mapped[date | None] = mapped_column(Date, index=True)
    source_kind: Mapped[str | None] = mapped_column(String(40), index=True)
    source_ref_id: Mapped[str | None] = mapped_column(String(160))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_version: Mapped[str] = mapped_column(String(40))
    is_scenario: Mapped[bool] = mapped_column(Boolean, default=False)


class Conflict(Base, TimestampMixin):
    __tablename__ = "conflict"

    conflict_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    calculation_id: Mapped[str] = mapped_column(ForeignKey("calculation_result.calculation_id"))
    financial_event_id: Mapped[str] = mapped_column(
        ForeignKey("financial_event.financial_event_id")
    )
    transaction_event_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("transaction_financial_event_link.transaction_event_link_id")
    )
    gap_days: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(10))
    priority_rank: Mapped[int | None] = mapped_column(Integer)
    conflict_origin: Mapped[str | None] = mapped_column(String(40))
    link_status: Mapped[str | None] = mapped_column(String(24))
    impact_level: Mapped[str | None] = mapped_column(String(24))
    days_until_event: Mapped[int | None] = mapped_column(Integer)
    same_day_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MonitoringRun(Base, TimestampMixin):
    __tablename__ = "monitoring_run"

    monitoring_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    new_alert_count: Mapped[int] = mapped_column(Integer, default=0)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DailyMonitoringReport(Base, TimestampMixin):
    """Persisted portfolio report produced from one reviewed monitoring snapshot."""

    __tablename__ = "daily_monitoring_report"

    daily_report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monitoring_run_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_run.monitoring_run_id"), index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    highest_priority: Mapped[str | None] = mapped_column(String(10))
    risk_case_count: Mapped[int] = mapped_column(Integer, default=0)
    asset_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Alert(Base, TimestampMixin):
    __tablename__ = "alert"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("trade_case.case_id"))
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    signal_code: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Confirmation(Base, TimestampMixin):
    __tablename__ = "confirmation"

    confirmation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(120), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("trade_case.case_id"))
    question_type: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")


class Report(Base, TimestampMixin):
    __tablename__ = "report"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("trade_case.case_id"))
    audience: Mapped[str] = mapped_column(String(40))
    basis_version: Mapped[str] = mapped_column(String(80))
    asset_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class TraceEvent(Base, TimestampMixin):
    __tablename__ = "trace_event"

    trace_event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(120))
    span_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    input_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timing_ms: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
