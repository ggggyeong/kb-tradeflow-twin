"""Existing-result export must remain read-only with respect to AI/data services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import verify_rag


def review_payload(mode: str) -> dict[str, Any]:
    return {
        "run_id": "synthetic-export-case",
        "mode": mode,
        "conflicts": [
            {
                "event_id": "TEST-LOAN",
                "event_name": "대출 만기",
                "reason": "만기가 먼저 도래합니다.",
            }
        ],
        "retrieval_trace": [
            {
                "event_id": "TEST-LOAN",
                "query_id": "loan_repayment_risk",
                "query": "원금 미상환 시 어떤 내용을 확인하나요?",
                "status": "SEARCHED",
                "unique_candidate_count": 3,
                "results": [
                    {
                        "source_file": "synthetic.pdf",
                        "page": 2,
                        "excerpt": "합성 테스트 근거입니다.",
                        "score": 0.7,
                    }
                ],
            }
        ],
        "product_options": [
            {
                "event_id": "TEST-LOAN",
                "product_name": "합성 설명서",
                "financial_institution": "TEST BANK",
                "explanation_points": [
                    {
                        "question": "상환 조건은 무엇인가요?",
                        "text": "합성 자료의 상환 조건을 확인합니다.",
                        "supporting_quote": "합성 테스트 근거입니다.",
                        "citations": [{"source_file": "synthetic.pdf", "page": 2}],
                    }
                ],
            }
        ],
        "checks": {"synthetic_check": True},
        "warnings": ["합성 테스트 기록입니다."],
    }


def forbid_external_work(*args: Any, **kwargs: Any) -> None:
    raise AssertionError(
        "Existing-result export must not load credentials, call a model, or search"
    )


def block_external_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_rag.LLMSettings, "from_env", forbid_external_work)
    monkeypatch.setattr(verify_rag, "RecordedLiveModel", forbid_external_work)
    monkeypatch.setattr(verify_rag, "FinancialRetrieval", forbid_external_work)
    monkeypatch.setattr(verify_rag, "FinanceAdvisorAgent", forbid_external_work)
    monkeypatch.setattr(verify_rag, "fixture_request", forbid_external_work)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret-must-not-print")


@pytest.mark.parametrize(
    "mode", ["live_full_pipeline", "live_finance_agent", "local_retrieval_only"]
)
def test_review_labels_full_pipeline_and_rag_only_without_conflating_ocr(
    tmp_path: Path,
    mode: str,
) -> None:
    target = tmp_path / f"{mode}.md"
    verify_rag.write_review(review_payload(mode), target)
    text = target.read_text(encoding="utf-8")
    full_notice = "PDF/OCR·금융일정 Excel을 함께 처리한 전체 실제 실행 기록입니다."
    rag_notice = "합성 거래의 예상 입금일을 입력한 RAG 단독 검증입니다. OCR 검증과 구분합니다."
    assert (full_notice in text) == (mode == "live_full_pipeline")
    assert (rag_notice in text) == (mode != "live_full_pipeline")
    assert "원금 미상환 시 어떤 내용을 확인하나요?" in text
    assert "질문: 상환 조건은 무엇인가요?" in text
    assert "출처: synthetic.pdf p.2" in text
    assert "금융 의미의 정확성을 보증하지 않습니다" in text


def test_from_result_writes_only_markdown_and_never_loads_secrets_or_calls_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    block_external_work(monkeypatch)
    payload = review_payload("ignored-mode-from-input")
    payload["verification"] = {
        "live_llm_executed": True,
        "run_id": "synthetic-export-case",
        "checks": {"retained_check": True},
    }
    original = tmp_path / "existing-result.json"
    original_text = json.dumps(payload, ensure_ascii=False)
    original.write_text(original_text, encoding="utf-8")
    destination = tmp_path / "review"
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_rag.py",
            "--from-result",
            str(original),
            "--output-directory",
            str(destination),
        ],
    )
    assert verify_rag.main() == 0
    target = destination / "synthetic-export-case-full-review.md"
    assert list(destination.iterdir()) == [target]
    text = target.read_text(encoding="utf-8")
    assert "live_full_pipeline" in text
    assert "전체 실제 실행 기록" in text
    assert "retained_check: True" in text
    assert original.read_text(encoding="utf-8") == original_text
    output = capsys.readouterr()
    assert "no API calls" in output.out
    assert "synthetic-secret-must-not-print" not in output.out + output.err + text


def test_from_result_rejects_live_flag_before_reading_file_or_calling_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    block_external_work(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_rag.py",
            "--from-result",
            str(tmp_path / "does-not-exist.json"),
            "--live",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        verify_rag.main()
    assert exc.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_from_result_rejects_mock_result_without_claiming_actual_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    block_external_work(monkeypatch)
    original = tmp_path / "mock-result.json"
    original.write_text(
        json.dumps({"verification": {"live_llm_executed": False}}), encoding="utf-8"
    )
    destination = tmp_path / "review"
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_rag.py",
            "--from-result",
            str(original),
            "--output-directory",
            str(destination),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        verify_rag.main()
    assert exc.value.code == 2
    assert "requires an actual live pipeline result" in capsys.readouterr().err
    assert not destination.exists()


@pytest.mark.parametrize("run_id", ["../escape", "/tmp/escape", "", "a/../escape"])
def test_from_result_rejects_unsafe_run_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    block_external_work(monkeypatch)
    original = tmp_path / "result.json"
    original.write_text(json.dumps({"verification": {"live_llm_executed": True, "run_id": run_id}}))
    destination = tmp_path / "review"
    monkeypatch.setattr(
        "sys.argv",
        ["verify_rag.py", "--from-result", str(original), "--output-directory", str(destination)],
    )
    with pytest.raises(SystemExit) as exc:
        verify_rag.main()
    assert exc.value.code == 2
    assert not destination.exists()
