from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.db.models import Base


def test_document_fact_schema_avoids_duplicate_typed_document_tables() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert len(tables) == 20
    assert "consultation_handoff" not in tables
    assert "daily_monitoring_execution" not in tables
    assert {"document", "document_fact", "trade_case", "shipment", "payment_obligation"} <= tables
    assert {"monitoring_run", "daily_monitoring_report"} <= tables
    assert {
        "booking_data",
        "bill_of_lading_data",
        "commercial_invoice_data",
        "case_candidate",
    }.isdisjoint(tables)

    company_columns = {column["name"] for column in inspector.get_columns("company")}
    assert company_columns == {
        "company_id",
        "legal_name",
        "product_advisory_profile_json",
        "created_at",
    }

    document_columns = {column["name"] for column in inspector.get_columns("document")}
    assert "source_references_json" in document_columns
    assert {"normalized_fields_json", "source_hash"}.isdisjoint(document_columns)

    fact_columns = {column["name"] for column in inspector.get_columns("document_fact")}
    assert fact_columns == {
        "fact_id",
        "document_id",
        "exact_standard_field",
        "raw_value",
        "normalized_json",
        "evidence_source",
        "created_at",
    }

    trade_case_columns = {column["name"] for column in inspector.get_columns("trade_case")}
    assert "contract_amount" not in trade_case_columns

    shipment_columns = {column["name"] for column in inspector.get_columns("shipment")}
    assert {
        "eta",
        "bl_issue_date",
        "booking_remarks",
        "last_monitor_state_version",
    }.isdisjoint(shipment_columns)

    timeline_columns = {
        column["name"] for column in inspector.get_columns("financial_transaction_timeline")
    }
    assert {"invoice_amount", "invoice_currency"} <= timeline_columns

    report_columns = {column["name"] for column in inspector.get_columns("daily_monitoring_report")}
    assert {
        "daily_report_id",
        "monitoring_run_id",
        "as_of_date",
        "summary_json",
        "highest_priority",
        "risk_case_count",
        "asset_path",
        "content_hash",
        "payload_hash",
        "dedup_key",
        "created_at",
    } <= report_columns
