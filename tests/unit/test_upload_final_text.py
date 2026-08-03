from app.graphs.common_control import _final_text


def test_upload_final_text_lists_candidate_document_counts_without_critic_verdict() -> None:
    answer = _final_text(
        {
            "mission_type": "UPLOAD_ANALYSIS",
            "task_results": {
                "stage_document_intelligence": {
                    "documents": [
                        {
                            "document_id": "DOC-BOOKING",
                            "classification": {"doc_type": "BOOKING_CONFIRMATION"},
                        },
                        {
                            "document_id": "DOC-INVOICE",
                            "classification": {"doc_type": "COMMERCIAL_INVOICE"},
                        },
                        {
                            "document_id": "DOC-BL",
                            "classification": {"doc_type": "BILL_OF_LADING"},
                        },
                    ]
                },
                "bundle_trade_cases": {
                    "cases": [
                        {
                            "case_id": "CASE-DEMO0",
                            "document_ids": ["DOC-BOOKING", "DOC-INVOICE", "DOC-BL"],
                        }
                    ]
                },
                "commit_trade_cases": {
                    "committed_case_ids": ["CASE-DEMO0"],
                    "pending_case_ids": [],
                },
                "evidence_review": {"verdict": "PASS"},
            },
        }
    )

    assert answer == (
        "무역 문서 분석을 완료했습니다. 거래 후보 1건입니다.\n\n"
        "- CASE-DEMO0: Booking Confirmation 1개, Commercial Invoice 1개, B/L 1개\n\n"
        "DB 반영 1건, 대기 0건입니다."
    )
    assert "Critic" not in answer
    assert "PASS" not in answer


def test_upload_final_text_reports_zero_for_a_missing_document_type() -> None:
    answer = _final_text(
        {
            "mission_type": "UPLOAD_ANALYSIS",
            "task_results": {
                "stage_document_intelligence": {
                    "documents": [
                        {
                            "document_id": "DOC-BOOKING",
                            "classification": {"doc_type": "BOOKING_CONFIRMATION"},
                        },
                        {
                            "document_id": "DOC-INVOICE",
                            "classification": {"doc_type": "COMMERCIAL_INVOICE"},
                        },
                    ]
                },
                "bundle_trade_cases": {
                    "cases": [
                        {
                            "case_id": "CASE-NO-BL",
                            "document_ids": ["DOC-BOOKING", "DOC-INVOICE"],
                        }
                    ]
                },
                "commit_trade_cases": {
                    "committed_case_ids": [],
                    "pending_case_ids": ["CASE-NO-BL"],
                },
            },
        }
    )

    assert (
        "CASE-NO-BL: Booking Confirmation 1개, Commercial Invoice 1개, B/L 0개"
        in answer
    )
    assert "DB 반영 0건, 대기 1건입니다." in answer


def test_financial_calendar_final_text_reports_storage_without_risk_analysis() -> None:
    answer = _final_text(
        {
            "mission_type": "FINANCIAL_CALENDAR_IMPORT",
            "task_results": {
                "import_financial_calendar": {
                    "event_count": 2,
                    "link_count": 2,
                    "created": {"events": 2, "links": 2},
                }
            },
        }
    )

    assert answer == (
        "업로드를 완료했습니다. 금융 이벤트 2건, 거래 연결 2건을 "
        "캘린더 DB에 반영했습니다."
    )
    assert "위험 분석" not in answer
