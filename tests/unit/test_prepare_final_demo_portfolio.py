from __future__ import annotations

import json

import pytest

from scripts import prepare_final_demo_portfolio as portfolio


def test_minimal_financial_seed_is_normalized_to_domain_contract(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portfolio, "PROJECT_ROOT", tmp_path)
    path = tmp_path / "financial_calendar_seed.json"
    path.write_text(
        json.dumps(
            {
                "company_id_from_authenticated_session": "DEMO1-CO",
                "contract_version": "demo-financial-calendar-minimal-v1",
                "sheets": {
                    "1.금융이벤트": {
                        "headers": [
                            "event_id",
                            "event_type_code",
                            "event_date",
                            "amount",
                            "currency",
                            "financial_institution",
                        ],
                        "rows": [
                            [
                                "EVT-DEMO1-LOAN",
                                "WORKING_CAPITAL_LOAN_MATURITY",
                                "2026-08-21",
                                75000000,
                                "krw",
                                "KB국민은행",
                            ]
                        ],
                    },
                    "2.거래연결": {
                        "headers": [
                            "transaction_id",
                            "event_id",
                            "link_type",
                            "link_status",
                        ],
                        "rows": [
                            [
                                "TXN-DEMO1",
                                "EVT-DEMO1-LOAN",
                                "REPAYMENT_SOURCE",
                                "CONFIRMED",
                            ]
                        ],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    contract = portfolio._normalize_financial_seed(path)

    assert contract.company_id == "DEMO1-CO"
    assert contract.events[0].event_name == "WORKING_CAPITAL_LOAN_MATURITY"
    assert contract.events[0].currency == "KRW"
    assert contract.events[0].is_kb_contract is True
    assert contract.links[0].transaction_id == "TXN-DEMO1"
    assert contract.links[0].link_id.startswith("LINK-")
    assert contract.links[0].dependency_scope == "UNKNOWN"


def test_minimal_financial_seed_rejects_cross_company_rows(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portfolio, "PROJECT_ROOT", tmp_path)
    path = tmp_path / "financial_calendar_seed.json"
    path.write_text(
        json.dumps(
            {
                "sheets": [
                    {
                        "sheet_name": "금융이벤트",
                        "rows": [
                            {
                                "company_id": "COMPANY-A",
                                "event_id": "EVENT-1",
                            }
                        ],
                    },
                    {
                        "sheet_name": "거래연결",
                        "rows": [
                            {
                                "company_id": "COMPANY-B",
                                "transaction_id": "TXN-1",
                                "event_id": "EVENT-1",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identical company_id"):
        portfolio._normalize_financial_seed(path)


def test_demo_selector_is_stable() -> None:
    assert portfolio.selected_demo_ids("all") == ("0", "1", "2")
    assert portfolio.selected_demo_ids("2") == ("2",)
    with pytest.raises(ValueError, match="Unsupported demo selector"):
        portfolio.selected_demo_ids("9")
