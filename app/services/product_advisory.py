from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Company,
    Document,
    DocumentFact,
    DocumentFieldOverride,
    PaymentObligation,
    TradeCase,
)
from app.services.knowledge import search_product_pages

PRODUCT_CATALOG_PATH = PROJECT_ROOT / "data" / "knowledge" / "product_catalog.json"
PRODUCT_CATALOG_SCHEMA = "kb-product-catalog-v1"

# Stable machine values remain in Agent/Tool payloads, while every user-facing
# surface uses these Korean labels.  Keeping both fields preserves API and audit
# compatibility without leaking implementation-oriented Enum values into Chat
# or reports.
PRODUCT_AVAILABILITY_LABELS = {
    "AVAILABLE_NOW": "현재 상담 검토 가능",
    "AVAILABLE_AFTER_BL": "B/L 수령 후 검토 가능",
    "ELIGIBILITY_UNKNOWN": "추가 요건 확인 필요",
    "NOT_APPLICABLE": "현재 상황에 적용 어려움",
}

PRODUCT_REQUIREMENT_LABELS = {
    "OVERSEAS_FOREIGN_CURRENCY_REAL_DEMAND": "해외 외화 실수요 확인",
    "IMPORT_PAYMENT": "수입대금 결제 목적 확인",
    "NON_LC_EXPORT_RECEIVABLE": "무신용장 방식 수출채권 확인",
    "EXPORT_RECEIVABLE_EXISTS": "수출채권 존재 확인",
    "PARTNER_MARKET_OR_PG_SELLER": "KB 제휴 마켓 또는 PG 셀러 여부 확인",
    "KB_CREDIT_GRADE_REQUIREMENT": "KB 신용등급 요건 확인",
    "STRATEGIC_TARGET_COMPANY": "전략 타깃 기업 요건 확인",
    "KB_CARD_SETTLEMENT_ACCOUNT": "KB 카드가맹점 결제계좌 확인",
    "THREE_CONSECUTIVE_MONTHS_CARD_SALES": "최근 3개월 연속 카드매출 확인",
    "KSURE_PROGRAM_ELIGIBILITY": "K-SURE 지원 프로그램 요건 확인",
}

PRODUCT_REQUIREMENT_SUMMARIES = {
    "STRATEGIC_TARGET_COMPANY": (
        "우수 기술력 등을 보유한 전략 타깃 중소기업에 해당하는지 확인합니다."
    ),
    "KSURE_PROGRAM_ELIGIBILITY": (
        "K-SURE 보증서·보험증권을 활용할 수 있는 지원 대상인지 확인합니다."
    ),
}

_SCENARIO_TERMS = {
    "WORKING_CAPITAL_LOAN_MATURITY": {
        "운전자금",
        "대출",
        "대출만기",
        "유동성",
        "원재료",
        "임금",
        "working_capital_loan_maturity",
        "loan_repayment",
    },
    "SUPPLIER_PAYMENT": {
        "공급업체",
        "수입대금",
        "수출대금",
        "매출채권",
        "팩토링",
        "usance",
        "유산스",
        "supplier_payment",
    },
    "FX_FORWARD_MATURITY": {
        "외화",
        "환율",
        "선물환",
        "외환",
        "fx",
        "fx_forward_maturity",
    },
}

_BORROWER_TYPES = {"CORPORATION", "SOLE_PROPRIETOR"}
_MAX_REQUIREMENT_QUESTIONS = 3


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def product_availability_label(value: str) -> str:
    """Return a safe Korean display label for one internal availability value."""
    return PRODUCT_AVAILABILITY_LABELS.get(
        str(value or "").strip().upper(),
        "상품 상태 확인 필요",
    )


def product_requirement_labels(values: list[str] | None) -> list[str]:
    """Translate internal requirement codes without changing the audit payload."""
    return [
        PRODUCT_REQUIREMENT_LABELS.get(
            str(value).strip().upper(),
            "기타 상품 요건 확인 필요",
        )
        for value in (values or [])
        if str(value).strip()
    ]


def product_payload_for_user(value: Any) -> Any:
    """Return a display copy that hides internal product enum codes.

    The original Tool/API payload remains unchanged for audit and machine use.
    Only dictionaries that already contain their Korean display counterpart are
    stripped, so unrelated business fields are preserved verbatim.
    """
    if isinstance(value, list):
        return [product_payload_for_user(item) for item in value]
    if not isinstance(value, dict):
        return value

    display = {key: product_payload_for_user(item) for key, item in value.items()}
    # These objects exist for machine-policy enforcement and rejection audit,
    # not for customer-facing explanations.
    display.pop("source_policy", None)
    display.pop("availability_values", None)
    display.pop("rejected", None)
    if display.get("availability_label"):
        display.pop("availability", None)
    if "missing_requirement_labels" in display:
        raw_labels = display.pop("missing_requirement_labels")
        if isinstance(raw_labels, list):
            labels = [str(item) for item in raw_labels]
        elif raw_labels:
            labels = [str(raw_labels)]
        else:
            labels = []
        display.pop("missing_requirements", None)
        display["missing_requirements_display"] = (
            ", ".join(labels) if labels else "없음(현재 입력 정보 기준)"
        )
    return display


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    payload = json.loads(PRODUCT_CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != PRODUCT_CATALOG_SCHEMA:
        raise ValueError(f"Unsupported product catalog schema: {payload.get('schema_version')}")
    products = payload.get("products", [])
    if not products:
        raise ValueError("Product catalog contains no products")
    return payload


def infer_scenario_codes(query: str) -> list[str]:
    """Infer broad liquidity scenarios without asserting product eligibility."""
    lowered = _normalized(query).casefold()
    selected = [
        code
        for code, terms in _SCENARIO_TERMS.items()
        if any(term.casefold() in lowered for term in terms)
    ]
    return selected or list(_SCENARIO_TERMS)


def _identity(value: Any) -> str:
    """Normalize a company/document party for conservative exact comparison."""
    return re.sub(r"[^0-9a-z가-힣]", "", _normalized(str(value or "")).casefold())


def _value(payload: Any) -> Any:
    if isinstance(payload, dict) and "value" in payload:
        return payload["value"]
    return payload


def _financial_context_text(context: dict[str, Any] | None) -> str:
    """Create a bounded deterministic retrieval context from a risk snapshot."""
    if not isinstance(context, dict) or context.get("status") == "NO_SNAPSHOT":
        return ""
    expected = context.get("expected_receipt") or {}
    parts = [
        f"case {context.get('case_id', '')}",
        f"최고위험 {context.get('highest_priority', '')}",
        (
            "예상입금 "
            f"{expected.get('date', '')} {expected.get('amount', '')} "
            f"{expected.get('currency', '')}"
        ),
    ]
    for conflict in list(context.get("conflicts") or [])[:5]:
        if not isinstance(conflict, dict):
            continue
        parts.append(
            " ".join(
                str(conflict.get(key) or "")
                for key in (
                    "event_type",
                    "event_name",
                    "impact_level",
                    "response_priority_level",
                    "reason",
                )
            )
        )
    return " | ".join(part.strip() for part in parts if part.strip())


def update_company_product_profile(
    session: Session,
    *,
    company_id: str,
    borrower_type: str | None = None,
    case_id: str | None = None,
    requirement_code: str | None = None,
    requirement_satisfied: bool | None = None,
    actor: str,
) -> dict[str, Any]:
    """Persist only explicit Human answers used by Product Advisor filters."""
    company = session.get(Company, company_id)
    if company is None:
        raise KeyError(f"Unknown company_id: {company_id}")
    profile = dict(company.product_advisory_profile_json or {})
    if borrower_type is not None:
        normalized_borrower = borrower_type.strip().upper()
        if normalized_borrower not in _BORROWER_TYPES:
            raise ValueError(f"Unsupported borrower_type: {borrower_type}")
        profile["borrower_type"] = normalized_borrower
    if requirement_code is not None:
        code = requirement_code.strip().upper()
        if code not in PRODUCT_REQUIREMENT_LABELS or requirement_satisfied is None:
            raise ValueError(f"Unsupported product requirement answer: {requirement_code}")
        scopes = dict(profile.get("requirements_by_case") or {})
        scope_key = str(case_id or "__COMPANY__")
        scope = dict(scopes.get(scope_key) or {})
        known = {str(item).upper() for item in scope.get("known_facts", [])}
        not_met = {str(item).upper() for item in scope.get("not_met_facts", [])}
        if requirement_satisfied:
            known.add(code)
            not_met.discard(code)
        else:
            not_met.add(code)
            known.discard(code)
        scope["known_facts"] = sorted(known)
        scope["not_met_facts"] = sorted(not_met)
        scopes[scope_key] = scope
        profile["requirements_by_case"] = scopes
    profile["last_actor"] = actor
    company.product_advisory_profile_json = profile
    session.flush()
    return profile


class ProductAdvisoryService:
    """Filter product candidates, retrieve OCR evidence, and compose grounded options."""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def _document_context(
        self,
        *,
        case_id: str | None,
        company_id: str | None,
    ) -> dict[str, Any]:
        """Resolve role and safe product facts from committed case/document facts."""
        if self.session is None:
            return {
                "case_id": case_id,
                "company_id": company_id,
                "customer_role": "UNKNOWN",
                "borrower_type": "UNKNOWN",
                "known_facts": [],
                "not_met_facts": [],
                "sources": [],
            }
        trade_case = self.session.get(TradeCase, case_id) if case_id else None
        if trade_case is not None:
            if company_id and trade_case.company_id != company_id:
                raise ValueError(f"case_id {case_id} does not belong to company_id {company_id}")
            company_id = trade_case.company_id
        company = self.session.get(Company, company_id) if company_id else None
        profile = dict(company.product_advisory_profile_json or {}) if company else {}
        role = "UNKNOWN"
        sources: list[str] = []
        known: set[str] = {str(item).upper() for item in profile.get("known_facts", [])}
        not_met: set[str] = {str(item).upper() for item in profile.get("not_met_facts", [])}
        scopes = dict(profile.get("requirements_by_case") or {})
        for scope_key in (str(case_id or ""), "__COMPANY__"):
            if not scope_key:
                continue
            scope = dict(scopes.get(scope_key) or {})
            known.update(str(item).upper() for item in scope.get("known_facts", []))
            not_met.update(str(item).upper() for item in scope.get("not_met_facts", []))

        if trade_case is not None and company is not None:
            documents = list(
                self.session.scalars(
                    select(Document)
                    .where(Document.case_id == trade_case.case_id)
                    .order_by(Document.document_id)
                )
            )
            document_ids = [item.document_id for item in documents]
            facts_by_key: dict[tuple[str, str], Any] = {}
            if document_ids:
                for fact in self.session.scalars(
                    select(DocumentFact).where(DocumentFact.document_id.in_(document_ids))
                ):
                    facts_by_key[(fact.document_id, fact.exact_standard_field)] = (
                        _value(fact.normalized_json) or fact.raw_value
                    )
                for override in self.session.scalars(
                    select(DocumentFieldOverride)
                    .where(DocumentFieldOverride.document_id.in_(document_ids))
                    .order_by(
                        DocumentFieldOverride.created_at,
                        DocumentFieldOverride.override_id,
                    )
                ):
                    facts_by_key[(override.document_id, override.exact_standard_field)] = (
                        _value(override.normalized_json) or override.raw_input
                    )
            seller_values: set[str] = set()
            buyer_values: set[str] = set()
            for document in documents:
                for field in ("seller", "shipper"):
                    normalized = _identity(facts_by_key.get((document.document_id, field)))
                    if normalized:
                        seller_values.add(normalized)
                for field in ("buyer", "consignee"):
                    normalized = _identity(facts_by_key.get((document.document_id, field)))
                    if normalized:
                        buyer_values.add(normalized)
            company_identity = _identity(company.legal_name)
            seller_match = bool(company_identity and company_identity in seller_values)
            buyer_match = bool(company_identity and company_identity in buyer_values)
            if seller_match ^ buyer_match:
                role = "EXPORTER" if seller_match else "IMPORTER"
                sources.append("DOCUMENT_PARTY_MATCH")

            obligations = list(
                self.session.scalars(
                    select(PaymentObligation).where(PaymentObligation.case_id == trade_case.case_id)
                )
            )
            if role == "EXPORTER" and trade_case.invoice_no:
                known.add("EXPORT_RECEIVABLE_EXISTS")
                sources.append("TRADE_CASE_INVOICE")
            if role == "EXPORTER" and any(item.payment_method == "TT" for item in obligations):
                known.add("NON_LC_EXPORT_RECEIVABLE")
                sources.append("PAYMENT_OBLIGATION_TT")
            if role == "IMPORTER" and obligations:
                known.add("IMPORT_PAYMENT")
                sources.append("PAYMENT_OBLIGATION_IMPORT")
            if trade_case.currency and trade_case.currency.upper() != "KRW":
                known.add("OVERSEAS_FOREIGN_CURRENCY_REAL_DEMAND")
                sources.append("TRADE_CASE_FOREIGN_CURRENCY")

        return {
            "case_id": trade_case.case_id if trade_case else case_id,
            "company_id": company.company_id if company else company_id,
            "customer_role": role,
            "borrower_type": str(profile.get("borrower_type") or "UNKNOWN").upper(),
            "known_facts": sorted(known),
            "not_met_facts": sorted(not_met),
            "sources": sorted(set(sources)),
        }

    def match_scenario(
        self,
        *,
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
        stored = self._document_context(case_id=case_id, company_id=company_id)
        context_text = _financial_context_text(financial_context)
        retrieval_query = " | ".join(item for item in (query.strip(), context_text) if item)
        selected_codes = scenario_codes or infer_scenario_codes(retrieval_query)
        facts = {
            *stored["known_facts"],
            *(fact.strip().upper() for fact in (known_facts or []) if fact.strip()),
        }
        not_met = {
            *stored["not_met_facts"],
            *(fact.strip().upper() for fact in (not_met_facts or []) if fact.strip()),
        }
        explicit_role = customer_role.strip().upper()
        explicit_borrower = borrower_type.strip().upper()
        role = stored["customer_role"] if stored["customer_role"] != "UNKNOWN" else explicit_role
        borrower = (
            stored["borrower_type"]
            if stored["borrower_type"] in _BORROWER_TYPES
            else explicit_borrower
        )
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for product in _catalog()["products"]:
            matching_codes = sorted(set(product["scenario_codes"]) & set(selected_codes))
            if not matching_codes:
                continue
            reasons: list[str] = []
            if role != "UNKNOWN" and role not in product["eligible_roles"]:
                reasons.append(f"ROLE_NOT_ELIGIBLE:{role}")
            if borrower != "UNKNOWN" and borrower not in product["borrower_types"]:
                reasons.append(f"BORROWER_TYPE_NOT_ELIGIBLE:{borrower}")
            exclusions = sorted(set(product["hard_exclusions"]) & facts)
            reasons.extend(f"HARD_EXCLUSION:{item}" for item in exclusions)
            unmet_requirements = sorted(set(product["hard_requirements"]) & not_met)
            reasons.extend(f"HARD_REQUIREMENT_NOT_MET:{item}" for item in unmet_requirements)
            if reasons:
                rejected.append(
                    {
                        "product_id": product["product_id"],
                        "canonical_name": product["canonical_name"],
                        "reasons": reasons,
                    }
                )
                continue
            missing_requirements = sorted(set(product["hard_requirements"]) - facts)
            availability = (
                product["availability_rule"] if not missing_requirements else "ELIGIBILITY_UNKNOWN"
            )
            candidates.append(
                {
                    "product_id": product["product_id"],
                    "canonical_name": product["canonical_name"],
                    "scenario_codes": matching_codes,
                    "availability": availability,
                    "availability_label": product_availability_label(availability),
                    "missing_requirements": missing_requirements,
                    "missing_requirement_labels": product_requirement_labels(missing_requirements),
                    "verified_summary": product["verified_summary"],
                    "source_file": _normalized(product["source_file"]),
                    "primary_pages": list(product["primary_pages"]),
                    "evidence_refs": [
                        {
                            "source_file": _normalized(product["source_file"]),
                            "page": page,
                        }
                        for page in product["primary_pages"]
                    ],
                }
            )
        candidates.sort(
            key=lambda item: (
                len(item["missing_requirements"]),
                -len(item["scenario_codes"]),
                item["canonical_name"],
                item["product_id"],
            )
        )
        requirement_questions: list[dict[str, Any]] = []
        if borrower != "UNKNOWN" and not requirements_reviewed:
            question_codes: list[str] = []
            for candidate in candidates[:3]:
                for code in candidate["missing_requirements"]:
                    if code not in question_codes:
                        question_codes.append(code)
            for code in question_codes[:_MAX_REQUIREMENT_QUESTIONS]:
                requirement_questions.append(
                    {
                        "requirement_code": code,
                        "requirement_label": product_requirement_labels([code])[0],
                        "requirement_summary": PRODUCT_REQUIREMENT_SUMMARIES.get(code, ""),
                        "product_ids": [
                            item["product_id"]
                            for item in candidates
                            if code in item["missing_requirements"]
                        ],
                    }
                )
        return {
            "query": query,
            "retrieval_query": retrieval_query,
            "scenario_codes": selected_codes,
            "customer_role": role,
            "borrower_type": borrower,
            "known_facts": sorted(facts),
            "not_met_facts": sorted(not_met),
            "filter_context": {
                **stored,
                "customer_role": role,
                "borrower_type": borrower,
                "known_facts": sorted(facts),
                "not_met_facts": sorted(not_met),
            },
            "missing_filter_inputs": (["borrower_type"] if borrower not in _BORROWER_TYPES else []),
            "requirement_questions": requirement_questions,
            "product_ids": [item["product_id"] for item in candidates],
            "products": candidates,
            "rejected": rejected,
            "source_policy": _catalog()["source_policy"],
        }

    def retrieve_evidence(
        self,
        *,
        query: str,
        product_ids: list[str],
        matched_products: list[dict[str, Any]],
        top_k_per_product: int = 2,
    ) -> dict[str, Any]:
        if not 1 <= top_k_per_product <= 5:
            raise ValueError("top_k_per_product must be between 1 and 5")
        matched_ids = [str(product.get("product_id")) for product in matched_products]
        if matched_ids != [str(product_id) for product_id in product_ids]:
            raise ValueError("matched_products and product_ids must have the same ordered IDs")
        by_id = {str(product["product_id"]): product for product in _catalog()["products"]}
        evidence: list[dict[str, Any]] = []
        for product_id in product_ids:
            product = by_id.get(product_id)
            if product is None:
                raise ValueError(f"Unknown product_id: {product_id}")
            source_file = _normalized(product["source_file"])
            matches = search_product_pages(
                f"{query} {product['canonical_name']} {product['verified_summary']}",
                top_k=top_k_per_product,
                source_files={source_file},
                pages_by_source={source_file: {int(page) for page in product["primary_pages"]}},
            )
            for match in matches:
                evidence.append(
                    {
                        "product_id": product_id,
                        "canonical_name": product["canonical_name"],
                        **match,
                    }
                )
        retrieval = {
            "query": query,
            "product_ids": product_ids,
            "evidence": evidence,
            "evidence_ids": [item["evidence_id"] for item in evidence],
            "source_status": "LOCAL_OCR_INDEX",
        }
        composed = self.compose_options(
            query=query,
            matched_products=matched_products,
            evidence=evidence,
        )
        return {**retrieval, **composed}

    def compose_options(
        self,
        *,
        query: str,
        matched_products: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_by_product: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            evidence_by_product.setdefault(str(item["product_id"]), []).append(item)
        options: list[dict[str, Any]] = []
        for product in matched_products:
            product_id = str(product["product_id"])
            citations = evidence_by_product.get(product_id, [])
            if not citations:
                continue
            availability = str(product["availability"])
            missing_requirements = [str(item) for item in product.get("missing_requirements", [])]
            options.append(
                {
                    "product_id": product_id,
                    "canonical_name": product["canonical_name"],
                    "availability": availability,
                    "availability_label": product_availability_label(availability),
                    "missing_requirements": missing_requirements,
                    "missing_requirement_labels": product_requirement_labels(missing_requirements),
                    "why_consider": product["verified_summary"],
                    "citations": [
                        {
                            "source_file": item["source_file"],
                            "page": item["page"],
                            "source_sha256": item["source_sha256"],
                            "excerpt": item["excerpt"],
                        }
                        for item in citations
                    ],
                }
            )
        return {
            "query": query,
            "options": options,
            "evidence_ids": [
                str(item["evidence_id"])
                for citations in evidence_by_product.values()
                for item in citations
            ],
            "eligibility_notice": (
                "후보 상품의 실제 이용 가능 여부, 한도, 금리, 필요서류와 승인은 "
                "최신 약관 및 KB 직원 심사를 통해 확인해야 합니다."
            ),
            "source_status": "LOCAL_OCR_INDEX",
        }
