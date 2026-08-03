from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.db.session import session_scope
from app.services.knowledge import KnowledgeService
from app.services.product_advisory import ProductAdvisoryService


@tool
def match_product_scenario(
    query: str,
    scenario_codes: list[str] | None = None,
    customer_role: str = "UNKNOWN",
    borrower_type: str = "UNKNOWN",
    known_facts: list[str] | None = None,
    not_met_facts: list[str] | None = None,
    case_id: str | None = None,
    company_id: str | None = None,
    financial_context: dict[str, Any] | None = None,
    requirements_reviewed: bool = False,
) -> dict[str, Any]:
    """Resolve DB/document context and apply auditable product hard filters."""
    with session_scope() as session:
        return ProductAdvisoryService(session).match_scenario(
            query=query,
            scenario_codes=scenario_codes,
            customer_role=customer_role,
            borrower_type=borrower_type,
            known_facts=known_facts,
            not_met_facts=not_met_facts,
            case_id=case_id,
            company_id=company_id,
            financial_context=financial_context,
            requirements_reviewed=requirements_reviewed,
        )


@tool
def retrieve_product_evidence(
    query: str,
    product_ids: list[str],
    matched_products: list[dict[str, Any]],
    top_k_per_product: int = 2,
) -> dict[str, Any]:
    """Retrieve PDF evidence and return only grounded product options."""
    return ProductAdvisoryService().retrieve_evidence(
        query=query,
        product_ids=product_ids,
        matched_products=matched_products,
        top_k_per_product=top_k_per_product,
    )


@tool
def search_product_knowledge(query: str, top_k: int = 5) -> dict[str, Any]:
    """Search the checked-in KB product PDF OCR index with page-level citations."""
    with session_scope() as session:
        result = KnowledgeService(session).answer_general(query, source_scope="KB_ONLY")
        payload = result.model_dump(mode="json")
        payload["citations"] = payload["citations"][:top_k]
        payload["candidates"] = payload["candidates"][:top_k]
        payload["evidence_ids"] = payload["evidence_ids"][:top_k]
        return payload
