from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_settings
from app.db.models import (
    Alert,
    DailyMonitoringReport,
    FinancialEvent,
    Shipment,
    TraceEvent,
    TradeCase,
    TransactionFinancialEventLink,
)
from app.db.session import init_database, session_factory
from app.graphs.compiled import build_root_conversation_graph
from app.services.advisory import AdvisoryService
from app.services.financial_exposure import FinancialExposureService
from app.services.financial_reference import read_transaction_financial_reference
from app.services.ingestion.batch_service import (
    SUPPORTED_UPLOAD_SUFFIXES,
    UPLOAD_ROOT,
    BatchService,
    upload_batch_directory,
    uploaded_document_paths,
)
from app.services.langgraph_agent_server import (
    LangGraphAgentServerError,
    create_chat_thread,
    resume_chat_thread,
    run_chat_thread,
)
from app.services.reports import REPORT_DIR
from app.services.virtual_clock import advance, current_date
from app.tools.shipment import build_shipment_snapshot

api_conversation_graph = build_root_conversation_graph(checkpointer=InMemorySaver())


class CommitRequest(BaseModel):
    confirmed_case_ids: list[str] = Field(default_factory=list)


class DocumentFieldOverrideRequest(BaseModel):
    document_id: str
    exact_standard_field: str
    value: str
    actor: str = "USER"
    reason: str | None = None


class ChatRequest(BaseModel):
    thread_id: str = "default"
    message: str
    company_id: str | None = None
    case_id: str | None = None
    source_scope: str = "KB_ONLY"
    workflow_kind: str | None = None
    batch_id: str | None = None
    workbook_path: str | None = None
    as_of_date: date | None = None
    reported_at: date | None = None
    delay_days: list[int] = Field(default_factory=list)
    customer_role: str | None = None
    borrower_type: str | None = None
    known_facts: list[str] | None = None
    not_met_facts: list[str] | None = None
    scenario_codes: list[str] = Field(default_factory=list)
    rm_inbox: str | None = None
    generate_report: bool = False


class ResumeRequest(BaseModel):
    issue_code: str
    value: Any


class GraphThreadRequest(BaseModel):
    source: str = "streamlit"


class DelayRequest(BaseModel):
    reported_at: date | None = None
    expected_delay_days: list[int] = Field(min_length=1)
    confirmed_anchor: str | None = None


class ClockRequest(BaseModel):
    days: int = Field(ge=0, le=3650)


def get_session() -> Any:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    init_database()
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="KB TradeFlow Twin",
    version="16.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": app.version,
        "mode": settings.tradeflow_mode,
        "openai_key_configured": settings.openai_api_key is not None,
        "langsmith_key_configured": settings.langsmith_api_key is not None,
        "virtual_date": current_date().isoformat(),
    }


@app.post("/api/graph/threads")
def create_graph_thread(body: GraphThreadRequest) -> dict[str, Any]:
    """Create the Agent Server thread shared by Streamlit and LangGraph Studio."""
    settings = get_settings()
    if settings.tradeflow_mode != "live":
        return {
            "thread_id": f"offline-{uuid.uuid4()}",
            "connected": False,
            "graph_id": settings.langgraph_graph_id,
            "backend": "local",
        }
    try:
        thread = create_chat_thread(metadata={"source": body.source})
    except LangGraphAgentServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    thread_id = str(thread.get("thread_id") or "").strip()
    if not thread_id:
        raise HTTPException(status_code=502, detail="LangGraph Thread ID가 없습니다.")
    return {
        "thread_id": thread_id,
        "connected": True,
        "graph_id": settings.langgraph_graph_id,
        "backend": "langgraph_agent_server",
    }


@app.post("/api/files/upload")
async def upload_files(
    batch_id: str,
    files: Annotated[list[UploadFile], File(...)],
) -> dict[str, Any]:
    try:
        target = upload_batch_directory(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_uploads: list[tuple[UploadFile, str, str]] = []
    for upload in files:
        name = Path(upload.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
            raise HTTPException(status_code=415, detail=f"Unsupported type: {name}")
        normalized_uploads.append((upload, name, suffix))

    existing_paths = target.iterdir() if target.is_dir() else ()
    existing_names = {
        path.name
        for path in existing_paths
        if path.is_file() and path.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES
    }
    projected_names = existing_names | {name for _, name, _ in normalized_uploads}
    projected_suffixes = {Path(name).suffix.lower() for name in projected_names}
    if len(projected_suffixes) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "One batch cannot mix trade-document PDFs and a financial-calendar XLSX; "
                "use separate batch IDs."
            ),
        )
    if projected_suffixes == {".xlsx"} and len(projected_names) != 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "A financial-calendar batch must contain exactly one 2-sheet XLSX "
                "(1.금융이벤트, 2.거래연결)."
            ),
        )

    target.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for upload, name, _ in normalized_uploads:
        path = target / name
        digest = hashlib.sha256()
        with path.open("wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
        saved.append({"file_name": name, "sha256": digest.hexdigest()})
    return {"batch_id": batch_id, "file_count": len(saved), "files": saved}


@app.post("/api/batches/{batch_id}/analyze")
def analyze_batch(batch_id: str, session: SessionDep) -> dict[str, Any]:
    try:
        paths = uploaded_document_paths(batch_id)
        return BatchService(session).analyze(paths, batch_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/batches/{batch_id}/commit")
def commit_batch(batch_id: str, body: CommitRequest, session: SessionDep) -> dict[str, Any]:
    try:
        return (
            BatchService(session)
            .commit(batch_id, set(body.confirmed_case_ids))
            .model_dump(mode="json")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Batch not found") from exc


@app.post("/api/batches/{batch_id}/field-overrides")
def apply_document_field_override(
    batch_id: str,
    body: DocumentFieldOverrideRequest,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return (
            BatchService(session)
            .apply_document_field_override(
                batch_id,
                body.document_id,
                body.exact_standard_field,
                body.value,
                actor=body.actor,
                reason=body.reason,
            )
            .model_dump(mode="json")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Batch or document not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/chat")
def chat(body: ChatRequest, session: SessionDep) -> dict[str, Any]:
    del session
    request_id = f"REQ-{uuid.uuid4().hex[:16]}"
    graph_input: dict[str, Any] = {
        "request_id": request_id,
        "thread_id": body.thread_id,
        "company_id": body.company_id,
        "planning_initialized": False,
        "messages": [{"role": "user", "content": body.message}],
        "case_ids": [body.case_id] if body.case_id else [],
        "source_scope": body.source_scope,
        "workflow_kind": body.workflow_kind,
        "batch_id": body.batch_id,
        "workbook_path": body.workbook_path,
        "as_of_date": body.as_of_date.isoformat() if body.as_of_date else None,
        "reported_at": (body.reported_at.isoformat() if body.reported_at else None),
        "delay_days": body.delay_days,
        "scenario_codes": body.scenario_codes,
        "rm_inbox": body.rm_inbox,
        "generate_report": body.generate_report,
    }
    # Do not overwrite reusable Thread/Company product-filter context with
    # UNKNOWN or an empty list on every new Chat request.
    if body.customer_role is not None:
        graph_input["customer_role"] = body.customer_role
    if body.borrower_type is not None:
        graph_input["borrower_type"] = body.borrower_type
    if body.known_facts is not None:
        graph_input["known_facts"] = body.known_facts
    if body.not_met_facts is not None:
        graph_input["not_met_facts"] = body.not_met_facts
    try:
        if get_settings().tradeflow_mode == "live":
            graph = run_chat_thread(body.thread_id, graph_input)
        else:
            graph = api_conversation_graph.invoke(
                graph_input,
                config={"configurable": {"thread_id": body.thread_id}},
            )
    except LangGraphAgentServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _graph_api_response(
        graph,
        request_id=request_id,
        thread_id=body.thread_id,
    )


def _graph_api_response(
    graph: dict[str, Any],
    *,
    request_id: str,
    thread_id: str,
) -> dict[str, Any]:
    interrupts = graph.get("__interrupt__", [])
    if interrupts:
        item = interrupts[0]
        payload = item.value if hasattr(item, "value") else item["value"]
        issue = payload.get("issue", payload) if isinstance(payload, dict) else payload
        return {
            "request_id": request_id,
            "thread_id": thread_id,
            "status": "HUMAN_REQUIRED",
            "issue": issue,
            "confirmation_id": (
                payload.get("confirmation_id") if isinstance(payload, dict) else None
            ),
        }
    final_response = graph.get("final_response")
    if final_response:
        return {
            "request_id": request_id,
            "thread_id": thread_id,
            **final_response,
        }
    messages = list(graph.get("messages", []))
    answer = (
        str(getattr(messages[-1], "content", messages[-1]))
        if messages
        else "응답을 생성하지 못했습니다."
    )
    return {
        "request_id": request_id,
        "thread_id": thread_id,
        "status": "SUCCESS",
        "answer": answer,
    }


@app.post("/api/threads/{thread_id}/resume")
def resume_thread(thread_id: str, body: ResumeRequest) -> dict[str, Any]:
    try:
        if get_settings().tradeflow_mode == "live":
            result = resume_chat_thread(thread_id, body.model_dump(mode="json"))
        else:
            result = api_conversation_graph.invoke(
                Command(resume=body.model_dump(mode="json")),
                config={"configurable": {"thread_id": thread_id}},
            )
    except LangGraphAgentServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _graph_api_response(
        cast(dict[str, Any], result),
        request_id=f"RESUME-{uuid.uuid4().hex[:16]}",
        thread_id=thread_id,
    )


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, session: SessionDep) -> dict[str, Any]:
    trade_case = session.get(TradeCase, case_id)
    if trade_case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    shipment = session.scalar(select(Shipment).where(Shipment.case_id == case_id))
    financial_reference = read_transaction_financial_reference(session, trade_case)
    linked_events: list[tuple[FinancialEvent, TransactionFinancialEventLink | None]]
    if trade_case.company_id and trade_case.transaction_id:
        linked_events = list(
            session.execute(
                select(FinancialEvent, TransactionFinancialEventLink)
                .join(
                    TransactionFinancialEventLink,
                    TransactionFinancialEventLink.financial_event_id
                    == FinancialEvent.financial_event_id,
                )
                .where(
                    TransactionFinancialEventLink.company_id == trade_case.company_id,
                    TransactionFinancialEventLink.transaction_id == trade_case.transaction_id,
                    or_(
                        TransactionFinancialEventLink.case_id == case_id,
                        TransactionFinancialEventLink.case_id.is_(None),
                    ),
                )
                .order_by(
                    FinancialEvent.event_date,
                    FinancialEvent.financial_event_id,
                )
            ).all()
        )
    else:
        linked_events = [
            (event, None)
            for event in session.scalars(
                select(FinancialEvent)
                .where(FinancialEvent.case_id == case_id)
                .order_by(
                    FinancialEvent.event_date,
                    FinancialEvent.financial_event_id,
                )
            )
        ]
    return {
        "case": {
            "case_id": trade_case.case_id,
            "company": trade_case.company,
            "counterparty": trade_case.counterparty,
            "invoice_no": trade_case.invoice_no,
            "contract_amount": financial_reference["amount"],
            "currency": trade_case.currency,
            "goods": trade_case.goods,
            "status": trade_case.status,
            "basis_version": trade_case.basis_version,
        },
        "shipment": build_shipment_snapshot(session, case_id) if shipment else None,
        "finance_events": [
            {
                "event_type": event.event_type,
                "financial_event_id": event.financial_event_id,
                "event_date": event.event_date,
                "amount": event.amount,
                "currency": event.currency,
                "direction": event.direction,
                "institution": event.institution,
                "facility_id": event.facility_id,
                "status": event.event_status,
                "verified": event.verified,
                "transaction_event_link_id": (link.transaction_event_link_id if link else None),
                "link_type": link.link_type if link else None,
                "link_status": link.link_status if link else None,
            }
            for event, link in linked_events
        ],
    }


@app.post("/api/cases/{case_id}/delay-advisory")
def delay_advisory(case_id: str, body: DelayRequest, session: SessionDep) -> dict[str, Any]:
    request_id = f"REQ-{uuid.uuid4().hex[:16]}"
    try:
        shipment = AdvisoryService(session).record_shipment_delay_scenarios(
            case_id,
            body.reported_at or current_date(),
            request_id,
            body.expected_delay_days,
        )
        financial = FinancialExposureService(session)
        gate = financial.inspect_payment_gate(case_id)
        if not gate.permitted and body.confirmed_anchor is None:
            return {
                "status": "HUMAN_REQUIRED",
                "shipment_analysis": shipment.model_dump(mode="json"),
                "payment_gate": gate.model_dump(mode="json"),
                "question": gate.customer_question,
            }
        exposure = financial.calculate_financial_exposure(
            case_id,
            f"{request_id}-financial",
            body.confirmed_anchor,
        )
        return {
            "status": "SUCCESS",
            "shipment_analysis": shipment.model_dump(mode="json"),
            "financial_exposure": exposure.model_dump(mode="json"),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/clock/advance")
def advance_clock(body: ClockRequest) -> dict[str, str]:
    return {"current_date": advance(body.days).isoformat()}


@app.get("/api/alerts")
def list_alerts(session: SessionDep) -> list[dict[str, Any]]:
    alerts = list(session.scalars(select(Alert).order_by(Alert.created_at.desc())))
    return [
        {
            "alert_id": alert.alert_id,
            "case_id": alert.case_id,
            "signal_code": alert.signal_code,
            "severity": alert.severity,
            "status": alert.status,
            "evidence": alert.evidence_json,
        }
        for alert in alerts
    ]


@app.get("/api/monitor/reports/latest")
def latest_daily_monitoring_report(session: SessionDep) -> dict[str, Any]:
    """Return the newest persisted daily summary without forcing a new run."""
    report = session.scalar(
        select(DailyMonitoringReport).order_by(
            DailyMonitoringReport.as_of_date.desc(),
            DailyMonitoringReport.created_at.desc(),
        )
    )
    if report is None:
        return {"report": None}
    return {
        "report": {
            "daily_report_id": report.daily_report_id,
            "monitoring_run_id": report.monitoring_run_id,
            "as_of_date": report.as_of_date.isoformat(),
            "summary": report.summary_json,
            "highest_priority": report.highest_priority,
            "risk_case_count": report.risk_case_count,
            "content_hash": report.content_hash,
            "created_at": report.created_at.isoformat(),
            "pdf_url": f"/api/monitor/reports/{report.daily_report_id}/pdf",
        }
    }


@app.get("/api/monitor/reports/{daily_report_id}/pdf")
def download_daily_monitoring_report(
    daily_report_id: str,
    session: SessionDep,
) -> FileResponse:
    """Download only a DB-registered asset inside the report directory."""
    report = session.get(DailyMonitoringReport, daily_report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Daily monitoring report not found")
    report_root = REPORT_DIR.resolve()
    path = Path(report.asset_path).resolve()
    if not path.is_relative_to(report_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Daily monitoring report asset not found")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/reports/{audience}/{case_id}")
def download_report(audience: str, case_id: str) -> FileResponse:
    normalized = audience.lower()
    if normalized not in {"customer", "rm"}:
        raise HTTPException(status_code=404, detail="Audience not found")
    path = PROJECT_ROOT / "data" / "reports" / f"{case_id}_{normalized}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not generated")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/debug/runs/{request_id}")
def debug_run(request_id: str, session: SessionDep) -> dict[str, Any]:
    traces = list(
        session.scalars(
            select(TraceEvent)
            .where(TraceEvent.request_id == request_id)
            .order_by(TraceEvent.trace_event_id)
        )
    )
    trace_id = next((trace.trace_id for trace in traces if trace.trace_id), None)
    project = get_settings().langsmith_project
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "langsmith_url": (
            f"https://smith.langchain.com/o/default/projects/p/{project}?trace={trace_id}"
            if trace_id
            else None
        ),
        "events": [
            {
                "span_type": trace.span_type,
                "name": trace.name,
                "input": trace.input_summary_json,
                "output": trace.output_summary_json,
                "timing_ms": trace.timing_ms,
                "metadata": trace.metadata_json,
            }
            for trace in traces
        ],
    }


@app.get("/api/openapi-summary")
def openapi_summary() -> dict[str, Any]:
    """Small route list used by smoke tests and the operator UI."""
    schema = app.openapi()
    return {
        "title": schema["info"]["title"],
        "version": schema["info"]["version"],
        "paths": sorted(schema["paths"]),
    }
