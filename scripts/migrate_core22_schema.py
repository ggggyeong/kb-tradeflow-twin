from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "tradeflow.db"
CORE_FIELDS: dict[str, set[str]] = {
    "BILL_OF_LADING": {
        "on_board_date",
        "bl_no",
        "port_of_loading",
        "vessel_name",
        "port_of_discharge",
        "shipper",
        "consignee",
        "voyage_no",
    },
    "BOOKING_CONFIRMATION": {
        "booking_no",
        "shipper",
        "vessel_name",
        "voyage_no",
        "port_of_loading",
        "port_of_discharge",
        "etd",
        "commodity",
    },
    "COMMERCIAL_INVOICE": {
        "invoice No.",
        "currency",
        "seller",
        "buyer",
        "description_of_goods",
        "payment_terms",
    },
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _drop_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    changed: list[str],
) -> None:
    if table in _tables(connection) and column in _columns(connection, table):
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        changed.append(f"{table}.{column}")


def _source_references(value: Any) -> str:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            payload = {}
    elif isinstance(value, dict):
        payload = value
    else:
        payload = {}
    references = payload.get("_source_references")
    return json.dumps(references if isinstance(references, dict) else {}, ensure_ascii=False)


def migrate(database_path: Path) -> dict[str, Any]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)

    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{database_path.stem}_pre_core22_{timestamp}.db"
    shutil.copy2(database_path, backup_path)

    changed: list[str] = []
    deleted_fact_count = 0
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")

        document_columns = _columns(connection, "document")
        if "source_references_json" not in document_columns:
            connection.execute(
                "ALTER TABLE document ADD COLUMN source_references_json JSON NOT NULL DEFAULT '{}'"
            )
            changed.append("document.source_references_json")
        if "normalized_fields_json" in document_columns:
            for row in connection.execute(
                "SELECT document_id, normalized_fields_json FROM document"
            ).fetchall():
                connection.execute(
                    "UPDATE document SET source_references_json = ? WHERE document_id = ?",
                    (_source_references(row["normalized_fields_json"]), row["document_id"]),
                )

        timeline_columns = _columns(connection, "financial_transaction_timeline")
        if "invoice_amount" not in timeline_columns:
            connection.execute(
                "ALTER TABLE financial_transaction_timeline ADD COLUMN invoice_amount FLOAT"
            )
            changed.append("financial_transaction_timeline.invoice_amount")
        if "invoice_currency" not in timeline_columns:
            connection.execute(
                "ALTER TABLE financial_transaction_timeline ADD COLUMN invoice_currency VARCHAR(12)"
            )
            changed.append("financial_transaction_timeline.invoice_currency")
        if "contract_amount" in _columns(connection, "trade_case"):
            connection.execute(
                """
                UPDATE financial_transaction_timeline
                SET invoice_amount = (
                        SELECT trade_case.contract_amount
                        FROM trade_case
                        WHERE trade_case.case_id = financial_transaction_timeline.case_id
                    ),
                    invoice_currency = COALESCE(
                        invoice_currency,
                        (
                            SELECT trade_case.currency
                            FROM trade_case
                            WHERE trade_case.case_id = financial_transaction_timeline.case_id
                        )
                    )
                WHERE invoice_amount IS NULL
                """
            )

        if {"document", "document_fact"} <= _tables(connection):
            allowed_pairs = [
                (doc_type, field)
                for doc_type, fields in CORE_FIELDS.items()
                for field in sorted(fields)
            ]
            allowed_sql = " OR ".join(
                "(document.doc_type = ? AND document_fact.exact_standard_field = ?)"
                for _ in allowed_pairs
            )
            parameters = [value for pair in allowed_pairs for value in pair]
            before = connection.execute("SELECT COUNT(*) FROM document_fact").fetchone()[0]
            connection.execute(
                f"""
                DELETE FROM document_fact
                WHERE fact_id IN (
                    SELECT document_fact.fact_id
                    FROM document_fact
                    JOIN document ON document.document_id = document_fact.document_id
                    WHERE NOT ({allowed_sql})
                )
                """,
                parameters,
            )
            after = connection.execute("SELECT COUNT(*) FROM document_fact").fetchone()[0]
            deleted_fact_count = int(before) - int(after)

        for table, columns in {
            "document": ["normalized_fields_json", "source_hash"],
            "document_fact": ["safe_key", "raw_label", "page", "bbox_json", "confidence"],
            "company": ["data_source", "profile_json", "source_metadata_json"],
            "trade_case": ["contract_amount"],
            "shipment": [
                "eta",
                "bl_issue_date",
                "booking_remarks",
                "last_monitor_state_version",
            ],
            "payment_obligation": ["computed_payment_date"],
        }.items():
            for column in columns:
                _drop_column(connection, table, column, changed)

        if "case_candidate" in _tables(connection):
            connection.execute("DROP TABLE case_candidate")
            changed.append("case_candidate")

        if "consultation_handoff" in _tables(connection):
            connection.execute("DROP TABLE consultation_handoff")
            changed.append("consultation_handoff")

        if "daily_monitoring_execution" in _tables(connection):
            connection.execute("DROP TABLE daily_monitoring_execution")
            changed.append("daily_monitoring_execution")

        # The monitoring calculation is now initiated explicitly from Chat.  Keep
        # the business result type independent from the removed scheduler and
        # migrate both indexed columns and immutable JSON evidence snapshots.
        if "calculation_result" in _tables(connection):
            source_rows = connection.execute(
                "SELECT COUNT(*) FROM calculation_result WHERE source_kind = 'SCHEDULED_SCAN'"
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE calculation_result
                SET source_kind = 'MONITORING_SCAN',
                    result_json = REPLACE(result_json, 'SCHEDULED_SCAN', 'MONITORING_SCAN'),
                    audit_json = REPLACE(audit_json, 'SCHEDULED_SCAN', 'MONITORING_SCAN')
                WHERE source_kind = 'SCHEDULED_SCAN'
                   OR result_json LIKE '%SCHEDULED_SCAN%'
                   OR audit_json LIKE '%SCHEDULED_SCAN%'
                """
            )
            if source_rows:
                changed.append(f"calculation_result.source_kind:{source_rows}")

        for table, json_column in {
            "alert": "evidence_json",
            "monitoring_run": "trace_json",
            "daily_monitoring_report": "summary_json",
        }.items():
            if table not in _tables(connection):
                continue
            changed_rows = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {json_column} LIKE '%SCHEDULED_SCAN%'"
            ).fetchone()[0]
            connection.execute(
                f"""
                UPDATE {table}
                SET {json_column} = REPLACE(
                    {json_column}, 'SCHEDULED_SCAN', 'MONITORING_SCAN'
                )
                WHERE {json_column} LIKE '%SCHEDULED_SCAN%'
                """
            )
            if changed_rows:
                changed.append(f"{table}.{json_column}:{changed_rows}")

        connection.execute("PRAGMA user_version = 2202")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"Foreign-key check failed: {foreign_key_errors}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "database_path": str(database_path),
        "backup_path": str(backup_path),
        "changed": changed,
        "deleted_non_core_document_facts": deleted_fact_count,
        "core_field_count": sum(len(fields) for fields in CORE_FIELDS.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate TradeFlow SQLite to Core-22 schema")
    parser.add_argument("database", nargs="?", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(json.dumps(migrate(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
