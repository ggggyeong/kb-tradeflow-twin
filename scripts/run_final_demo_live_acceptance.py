from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.config import PROJECT_ROOT
from app.db.models import Alert, CalculationResult, Conflict, MonitoringRun, Report
from app.db.session import session_scope

DEMO_ROOT = PROJECT_ROOT / "data" / "judge_demo_final"
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "final_live_acceptance"


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def _create_thread(client: httpx.Client) -> str:
    payload = _post(client, "/api/graph/threads", {"source": "final-live-acceptance"})
    thread_id = str(payload.get("thread_id") or "").strip()
    if not thread_id or payload.get("connected") is not True:
        raise RuntimeError("Agent Server did not return a thread_id")
    return thread_id


def _interrupt(state: dict[str, Any]) -> dict[str, Any] | None:
    if state.get("status") == "HUMAN_REQUIRED":
        issue = state.get("issue")
        if not isinstance(issue, dict) or not issue.get("issue_code"):
            raise AssertionError(f"Malformed API Human issue: {state!r}")
        return issue
    raw = state.get("__interrupt__", [])
    if not raw:
        return None
    first = raw[0]
    value = first.get("value", {}) if isinstance(first, dict) else getattr(first, "value", {})
    if not isinstance(value, dict):
        raise AssertionError(f"Malformed interrupt payload: {value!r}")
    issue = value.get("issue", value)
    if not isinstance(issue, dict) or not issue.get("issue_code"):
        raise AssertionError(f"Malformed Human issue: {value!r}")
    return issue


def _human_answer(issue: dict[str, Any]) -> Any:
    code = str(issue["issue_code"])
    if code.startswith("PAYMENT_ANCHOR_REQUIRED:"):
        return "ON_BOARD_DATE"
    if code.startswith("FINANCIAL_LINK_SCOPE_REQUIRED:"):
        return "FULL"
    if code == "REPORT_GENERATION_CONSENT":
        return True
    if code.startswith("PRODUCT_BORROWER_TYPE_REQUIRED:"):
        return "CORPORATION"
    if code.startswith("PRODUCT_REQUIREMENT:"):
        return False
    raise AssertionError(f"No acceptance answer for Human issue: {code}")


def _node_trace(client: httpx.Client, thread_id: str) -> list[str]:
    history = _post(client, f"/threads/{thread_id}/history", {"limit": 1000})
    nodes: list[str] = []
    for checkpoint in reversed(history):
        if not isinstance(checkpoint, dict):
            continue
        for task in checkpoint.get("tasks", []):
            name = str(task.get("name") or "")
            if name and not name.startswith("__") and (not nodes or nodes[-1] != name):
                nodes.append(name)
    return nodes


def _run(
    api_client: httpx.Client,
    graph_client: httpx.Client,
    *,
    thread_id: str,
    request_id: str,
    message: str,
    hints: dict[str, Any],
) -> dict[str, Any]:
    graph_input = {
        "message": message,
        "thread_id": thread_id,
        **hints,
    }
    state = _post(api_client, "/api/chat", graph_input)
    issues: list[dict[str, Any]] = []
    for _ in range(12):
        issue = _interrupt(state)
        if issue is None:
            break
        answer = _human_answer(issue)
        issues.append({"issue_code": issue["issue_code"], "answer": answer})
        state = _post(
            api_client,
            f"/api/threads/{thread_id}/resume",
            {"issue_code": issue["issue_code"], "value": answer},
        )
    else:
        raise AssertionError(f"{request_id}: more than 12 Human interrupts")
    if _interrupt(state) is not None:
        raise AssertionError(f"{request_id}: unresolved Human interrupt")
    final = state
    if final.get("status") != "SUCCESS":
        raise AssertionError(f"{request_id}: response={final}")
    return {
        "request_id": request_id,
        "thread_id": thread_id,
        "issues": issues,
        "node_trace": _node_trace(graph_client, thread_id),
        "final_response": final,
    }


def _upload_demo_zero(client: httpx.Client) -> str:
    suffix = hashlib.sha256(datetime.now(UTC).isoformat().encode()).hexdigest()[:12].upper()
    batch_id = f"FINAL-LIVE-DEMO0-{suffix}"
    source = DEMO_ROOT / "demo_0_complete_batch"
    paths = sorted(source.glob("*.pdf"))
    if len(paths) != 8:
        raise AssertionError(f"Demo 0 must contain 8 PDFs, found {len(paths)}")
    files = [
        ("files", (path.name, path.read_bytes(), "application/pdf"))
        for path in paths
    ]
    response = client.post(
        "/api/files/upload",
        params={"batch_id": batch_id},
        files=files,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("file_count") != 8:
        raise AssertionError(f"Demo 0 upload mismatch: {payload}")
    return batch_id


def _assert_demo_zero(record: dict[str, Any]) -> None:
    final = record["final_response"]
    result = final["results"]["commit_trade_cases"]
    if result["committed_case_ids"] != ["CASE-DEMO0", "CASE-DEMO0B", "CASE-DEMO0C"]:
        raise AssertionError(f"Demo 0 committed unexpected cases: {result}")


def _assert_monitoring(record: dict[str, Any]) -> None:
    finalized = record["final_response"]["results"]["finalize_manual_monitoring"]
    if finalized["priority_summary"]["ordered_case_ids"] != ["CASE-DEMO1"]:
        raise AssertionError(f"Demo 1 ranking mismatch: {finalized['priority_summary']}")
    if not Path(finalized["daily_report"]["asset_path"]).is_file():
        raise AssertionError("Demo 1 daily monitoring PDF was not created")


def _assert_delay(record: dict[str, Any]) -> None:
    codes = {str(item["issue_code"]) for item in record["issues"]}
    if "PAYMENT_ANCHOR_REQUIRED:CASE-DEMO2" not in codes:
        raise AssertionError(f"Demo 2 did not ask for its B/L anchor: {sorted(codes)}")
    if not any(code.startswith("FINANCIAL_LINK_SCOPE_REQUIRED:") for code in codes):
        raise AssertionError(f"Demo 2 did not ask for dependency_scope: {sorted(codes)}")
    results = record["final_response"]["results"]
    recalculated = [
        value
        for key, value in results.items()
        if str(key).startswith("recalculate_financial_exposure_")
    ]
    if not recalculated or int(recalculated[-1].get("conflict_count", 0)) < 1:
        raise AssertionError("Demo 2 did not persist a recalculated conflict")


def _assert_report(record: dict[str, Any], case_id: str) -> None:
    final = record["final_response"]
    customer = final["results"]["render_customer_report"]
    rm = final["results"]["render_rm_report"]
    if customer["case_id"] != case_id or rm["case_id"] != case_id:
        raise AssertionError(f"Report case mismatch for {case_id}")
    if customer["basis_version"] != rm["basis_version"]:
        raise AssertionError(f"Customer/RM basis mismatch for {case_id}")
    for result in (customer, rm):
        if not Path(result["asset_path"]).is_file():
            raise AssertionError(f"Missing report file: {result['asset_path']}")


def _database_summary() -> dict[str, int]:
    with session_scope() as session:
        return {
            "calculation_results": int(
                session.scalar(select(func.count()).select_from(CalculationResult)) or 0
            ),
            "conflicts": int(session.scalar(select(func.count()).select_from(Conflict)) or 0),
            "monitoring_runs": int(
                session.scalar(select(func.count()).select_from(MonitoringRun)) or 0
            ),
            "alerts": int(session.scalar(select(func.count()).select_from(Alert)) or 0),
            "reports": int(session.scalar(select(func.count()).select_from(Report)) or 0),
        }


def run_acceptance(api_base_url: str, graph_base_url: str) -> dict[str, Any]:
    with (
        httpx.Client(base_url=api_base_url.rstrip("/"), timeout=600) as api_client,
        httpx.Client(base_url=graph_base_url.rstrip("/"), timeout=600) as graph_client,
    ):
        health = api_client.get("/health")
        health.raise_for_status()
        if health.json().get("mode") != "live":
            raise AssertionError(f"FastAPI is not live: {health.json()}")
        batch_id = _upload_demo_zero(api_client)
        demo0_thread = _create_thread(api_client)
        demo0 = _run(
            api_client,
            graph_client,
            thread_id=demo0_thread,
            request_id="FINAL-LIVE-DEMO0",
            message="3",
            hints={
                "company_id": "DEMO0-CO",
                "workflow_kind": "UPLOAD_ANALYSIS",
                "batch_id": batch_id,
            },
        )
        _assert_demo_zero(demo0)

        demo1_thread = _create_thread(api_client)
        demo1 = _run(
            api_client,
            graph_client,
            thread_id=demo1_thread,
            request_id="FINAL-LIVE-DEMO1-RISK",
            message="2",
            hints={
                "company_id": "DEMO1-CO",
                "workflow_kind": "PROACTIVE_MONITORING",
                "as_of_date": "2026-08-20",
            },
        )
        _assert_monitoring(demo1)
        demo1_report = _run(
            api_client,
            graph_client,
            thread_id=demo1_thread,
            request_id="FINAL-LIVE-DEMO1-REPORT",
            message="CASE-DEMO1 고객용·RM용 충돌 종합보고서를 만들어줘.",
            hints={
                "company_id": "DEMO1-CO",
                "case_id": "CASE-DEMO1",
                "workflow_kind": "PRODUCT_ADVISORY_REPORT",
                "generate_report": True,
                "as_of_date": "2026-08-20",
            },
        )
        _assert_report(demo1_report, "CASE-DEMO1")

        demo2_thread = _create_thread(api_client)
        demo2 = _run(
            api_client,
            graph_client,
            thread_id=demo2_thread,
            request_id="FINAL-LIVE-DEMO2-DELAY",
            message=(
                "CASE-DEMO2 거래의 B/L이 계획보다 9일 늦어진다는 소식을 들었어. "
                "금융일정 충돌과 위험순위를 분석해줘."
            ),
            hints={
                "company_id": "DEMO2-CO",
                "case_id": "CASE-DEMO2",
                "workflow_kind": "USER_REPORTED_DELAY",
                "reported_at": "2026-08-20",
                "delay_days": [9],
            },
        )
        _assert_delay(demo2)
        demo2_report = _run(
            api_client,
            graph_client,
            thread_id=demo2_thread,
            request_id="FINAL-LIVE-DEMO2-REPORT",
            message="CASE-DEMO2 고객용·RM용 충돌 종합보고서를 만들어줘.",
            hints={
                "company_id": "DEMO2-CO",
                "case_id": "CASE-DEMO2",
                "workflow_kind": "PRODUCT_ADVISORY_REPORT",
                "generate_report": True,
                "as_of_date": "2026-08-20",
            },
        )
        _assert_report(demo2_report, "CASE-DEMO2")

    return {
        "status": "PASS",
        "mode": "live",
        "api_base_url": api_base_url,
        "graph_base_url": graph_base_url,
        "completed_at": datetime.now(UTC).isoformat(),
        "records": [demo0, demo1, demo1_report, demo2, demo2_report],
        "database": _database_summary(),
    }


def _compact(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    return {
        "status": payload["status"],
        "mode": payload["mode"],
        "output_path": str(output_path),
        "database": payload["database"],
        "runs": [
            {
                "request_id": record["request_id"],
                "thread_id": record["thread_id"],
                "issues": [item["issue_code"] for item in record["issues"]],
                "result_keys": sorted(record["final_response"].get("results", {})),
            }
            for record in payload["records"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run final Demo 0/1/2 against a live Agent Server")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--graph-base-url", default="http://127.0.0.1:2024")
    args = parser.parse_args()
    payload = run_acceptance(args.api_base_url, args.graph_base_url)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = OUTPUT_ROOT / f"final_live_acceptance_{timestamp}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(_compact(payload, output_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
