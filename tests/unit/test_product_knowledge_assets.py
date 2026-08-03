from __future__ import annotations

import hashlib
import json
import unicodedata

from app.core.config import PROJECT_ROOT
from app.services.product_advisory import (
    ProductAdvisoryService,
    product_availability_label,
    product_payload_for_user,
    product_requirement_labels,
)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def test_product_catalog_and_ocr_index_cover_actual_source_pdfs() -> None:
    knowledge_dir = PROJECT_ROOT / "data" / "knowledge"
    catalog = json.loads((knowledge_dir / "product_catalog.json").read_text(encoding="utf-8"))
    index = json.loads((knowledge_dir / "product_ocr_index.json").read_text(encoding="utf-8"))
    source_dir = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"

    actual = {
        _nfc(path.name): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path,
        )
        for path in source_dir.glob("*.pdf")
    }
    indexed_by_source: dict[str, list[dict[str, object]]] = {}
    for page in index["pages"]:
        indexed_by_source.setdefault(_nfc(str(page["sourceFile"])), []).append(page)

    assert index["schemaVersion"] == "product-ocr-index-v1"
    assert len(actual) == 9
    assert len(index["pages"]) == 48
    assert set(indexed_by_source) == set(actual)

    for source_name, pages in indexed_by_source.items():
        expected_hash, _ = actual[source_name]
        assert {str(page["sourceSha256"]) for page in pages} == {expected_hash}
        assert [int(page["page"]) for page in pages] == list(range(1, len(pages) + 1))
        assert all(str(page["text"]).strip() for page in pages)

    products = catalog["products"]
    assert len(products) == 9
    assert {product["source_file"] for product in products} == set(indexed_by_source)
    for product in products:
        page_count = len(indexed_by_source[product["source_file"]])
        assert all(1 <= int(page) <= page_count for page in product["primary_pages"])


def test_high_risk_product_filters_are_explicit() -> None:
    catalog_path = PROJECT_ROOT / "data" / "knowledge" / "product_catalog.json"
    products = {
        item["product_id"]: item
        for item in json.loads(catalog_path.read_text(encoding="utf-8"))["products"]
    }

    assert "REFINANCE_EXISTING_KB_LOAN" in products["ONE-KB-CORPORATE-LOAN"]["hard_exclusions"]
    assert products["KB-PAYMENT-USANCE"]["eligible_roles"] == ["IMPORTER"]
    assert "PARTNER_MARKET_OR_PG_SELLER" in products["KB-SELLER-LOAN"]["hard_requirements"]
    assert products["KB-OWNER-OVERDRAFT"]["borrower_types"] == ["SOLE_PROPRIETOR"]
    assert products["KB-EXPORT-FACTORING"]["availability_rule"] == "AVAILABLE_AFTER_BL"


def test_existing_kb_refinance_fact_rejects_one_kb_candidate() -> None:
    result = ProductAdvisoryService().match_scenario(
        query="운전자금 대출 만기",
        scenario_codes=["WORKING_CAPITAL_LOAN_MATURITY"],
        customer_role="EXPORTER",
        borrower_type="CORPORATION",
        known_facts=["REFINANCE_EXISTING_KB_LOAN"],
    )

    assert "ONE-KB-CORPORATE-LOAN" not in result["product_ids"]
    rejected = {item["product_id"]: item["reasons"] for item in result["rejected"]}
    assert rejected["ONE-KB-CORPORATE-LOAN"] == ["HARD_EXCLUSION:REFINANCE_EXISTING_KB_LOAN"]


def test_corporation_borrower_rejects_owner_overdraft() -> None:
    result = ProductAdvisoryService().match_scenario(
        query="공급업체 지급과 운전자금",
        scenario_codes=["SUPPLIER_PAYMENT"],
        customer_role="IMPORTER",
        borrower_type="CORPORATION",
    )

    assert "KB-OWNER-OVERDRAFT" not in result["product_ids"]
    rejected = {item["product_id"]: item["reasons"] for item in result["rejected"]}
    assert rejected["KB-OWNER-OVERDRAFT"] == ["BORROWER_TYPE_NOT_ELIGIBLE:CORPORATION"]


def test_financial_exposure_snapshot_becomes_product_retrieval_context() -> None:
    result = ProductAdvisoryService().match_scenario(
        query="이 거래에 맞는 KB 상품",
        customer_role="EXPORTER",
        borrower_type="CORPORATION",
        financial_context={
            "case_id": "CASE-001",
            "highest_priority": "P1",
            "conflicts": [
                {
                    "event_type": "FX_FORWARD_MATURITY",
                    "impact_level": "HIGH",
                    "reason": "예상 수출대금 회수일 이후 선물환 만기",
                }
            ],
        },
    )

    assert "FX_FORWARD_MATURITY" in result["scenario_codes"]
    assert "FX_FORWARD_MATURITY" in result["retrieval_query"]
    assert "예상 수출대금 회수일 이후 선물환 만기" in result["retrieval_query"]


def test_product_requirement_questions_include_short_customer_explanations() -> None:
    result = ProductAdvisoryService().match_scenario(
        query="운전자금 대출 만기",
        scenario_codes=["WORKING_CAPITAL_LOAN_MATURITY"],
        customer_role="EXPORTER",
        borrower_type="CORPORATION",
    )

    questions = {
        item["requirement_code"]: item["requirement_summary"]
        for item in result["requirement_questions"]
    }
    assert questions["STRATEGIC_TARGET_COMPANY"] == (
        "우수 기술력 등을 보유한 전략 타깃 중소기업에 해당하는지 확인합니다."
    )
    assert questions["KSURE_PROGRAM_ELIGIBILITY"] == (
        "K-SURE 보증서·보험증권을 활용할 수 있는 지원 대상인지 확인합니다."
    )


def test_composed_product_options_require_hash_validated_page_citations() -> None:
    service = ProductAdvisoryService()
    matched = service.match_scenario(
        query="수입대금 유산스",
        scenario_codes=["SUPPLIER_PAYMENT"],
        customer_role="IMPORTER",
        borrower_type="CORPORATION",
    )
    selected = matched["products"][:3]
    evidence = service.retrieve_evidence(
        query="수입대금 유산스",
        product_ids=[item["product_id"] for item in selected],
        matched_products=selected,
        top_k_per_product=1,
    )

    assert evidence["options"]
    assert all(option["citations"] for option in evidence["options"])
    assert all(option["availability_label"] for option in evidence["options"])
    assert all(
        len(option["missing_requirements"]) == len(option["missing_requirement_labels"])
        for option in evidence["options"]
    )
    assert all(
        citation["source_file"] and citation["page"] >= 1 and len(citation["source_sha256"]) == 64
        for option in evidence["options"]
        for citation in option["citations"]
    )


def test_product_availability_keeps_internal_enum_and_adds_korean_label() -> None:
    result = ProductAdvisoryService().match_scenario(
        query="무신용장 수출채권 팩토링",
        scenario_codes=["SUPPLIER_PAYMENT"],
        customer_role="EXPORTER",
        borrower_type="CORPORATION",
        known_facts=["NON_LC_EXPORT_RECEIVABLE", "EXPORT_RECEIVABLE_EXISTS"],
    )

    factoring = next(
        product for product in result["products"] if product["product_id"] == "KB-EXPORT-FACTORING"
    )
    assert factoring["availability"] == "AVAILABLE_AFTER_BL"
    assert factoring["availability_label"] == "B/L 수령 후 검토 가능"
    assert factoring["missing_requirements"] == []
    assert factoring["missing_requirement_labels"] == []


def test_product_display_mapping_covers_internal_values_and_hides_codes() -> None:
    assert product_availability_label("AVAILABLE_NOW") == "현재 상담 검토 가능"
    assert product_availability_label("AVAILABLE_AFTER_BL") == "B/L 수령 후 검토 가능"
    assert product_availability_label("ELIGIBILITY_UNKNOWN") == "추가 요건 확인 필요"
    assert product_availability_label("NOT_APPLICABLE") == "현재 상황에 적용 어려움"
    assert product_availability_label("UNREGISTERED") == "상품 상태 확인 필요"

    requirements = product_requirement_labels(
        ["EXPORT_RECEIVABLE_EXISTS", "KB_CREDIT_GRADE_REQUIREMENT"]
    )
    assert requirements == ["수출채권 존재 확인", "KB 신용등급 요건 확인"]

    raw = {
        "source_policy": {"availability_values": ["AVAILABLE_AFTER_BL"]},
        "options": [
            {
                "availability": "AVAILABLE_AFTER_BL",
                "availability_label": "B/L 수령 후 검토 가능",
                "missing_requirements": ["EXPORT_RECEIVABLE_EXISTS"],
                "missing_requirement_labels": ["수출채권 존재 확인"],
            }
        ],
    }
    display = product_payload_for_user(raw)
    assert display["options"][0] == {
        "availability_label": "B/L 수령 후 검토 가능",
        "missing_requirements_display": "수출채권 존재 확인",
    }
    assert "source_policy" not in display
    assert raw["options"][0]["availability"] == "AVAILABLE_AFTER_BL"

    empty = product_payload_for_user({"missing_requirements": [], "missing_requirement_labels": []})
    assert empty == {"missing_requirements_display": "없음(현재 입력 정보 기준)"}
