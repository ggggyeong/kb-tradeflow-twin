from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.domain_inputs.loaders.field_dictionary import load_field_registry
from app.domain_inputs.loaders.financial_priority import load_financial_priority_policy
from app.domain_inputs.loaders.payment_terms import load_payment_terms_contract
from app.schemas.field_contract import FieldRegistry
from app.schemas.financial_calendar import FinancialPriorityPolicy
from app.schemas.payment_terms import PaymentTermsContract

ACTIVE_DIR = PROJECT_ROOT / "data" / "domain_inputs" / "active"
KB_DOC_DIR = PROJECT_ROOT / "kb_doc"


def _kb_doc_workbook(normalized_name: str) -> Path:
    matches = [
        path
        for path in KB_DOC_DIR.glob("*.xlsx")
        if unicodedata.normalize("NFC", path.name) == normalized_name
    ]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {normalized_name!r} in {KB_DOC_DIR}, got {matches}"
        )
    return matches[0]


@lru_cache(maxsize=1)
def field_registry() -> FieldRegistry:
    """Return the reviewed Field Dictionary or fail closed."""
    return load_field_registry(ACTIVE_DIR / "document_field_dictionary.xlsx")


@lru_cache(maxsize=1)
def payment_terms_contract() -> PaymentTermsContract:
    """Return the reviewed PaymentTerms workbook or fail closed."""
    return load_payment_terms_contract(ACTIVE_DIR / "payment_terms_cases.xlsx")


@lru_cache(maxsize=1)
def financial_priority_policy() -> FinancialPriorityPolicy:
    """Return the reviewed deterministic priority policy."""
    return load_financial_priority_policy(_kb_doc_workbook("금융일정_우선순위_로직.xlsx"))


def validate_active_inputs() -> dict[str, str | int]:
    """Validate both required XLSX contracts during application startup."""
    registry = field_registry()
    payment = payment_terms_contract()
    return {
        "field_contract_sha256": registry.source_sha256,
        "field_count": len(registry.entries),
        "core_count": sum(entry.core_blocking_candidate for entry in registry.entries),
        "payment_terms_sha256": payment.source_sha256,
        "payment_example_count": len(payment.examples),
    }


def clear_provider_caches() -> None:
    """Clear providers for fail-closed mutation tests."""
    field_registry.cache_clear()
    payment_terms_contract.cache_clear()
    financial_priority_policy.cache_clear()


def required_input_paths() -> list[Path]:
    """Return the two inputs whose absence must prevent app startup."""
    return [
        ACTIVE_DIR / "document_field_dictionary.xlsx",
        ACTIVE_DIR / "payment_terms_cases.xlsx",
    ]
