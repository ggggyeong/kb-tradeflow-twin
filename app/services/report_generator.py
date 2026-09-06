from __future__ import annotations

import hashlib
import html
from functools import partial
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.paths import REPORT_OUTPUT_DIR
from app.schemas.portfolio import PortfolioReportPayload

DEFAULT_REPORT_DIR = REPORT_OUTPUT_DIR
FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
FONT_NAME = "TradeFlowKorean"
FALLBACK_FONT_NAME = "HYSMyeongJo-Medium"
NAVY = colors.HexColor("#12243D")
BLUE = colors.HexColor("#2F6FED")
GREEN = colors.HexColor("#25845E")
AMBER = colors.HexColor("#C88716")
RED = colors.HexColor("#B83F4B")
PALE = colors.HexColor("#F4F7FB")
LINE = colors.HexColor("#D8E0EA")
MUTED = colors.HexColor("#657184")


def _register_font() -> str:
    if FONT_PATH.is_file():
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        return FONT_NAME
    if FALLBACK_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_FONT_NAME))
    return FALLBACK_FONT_NAME


def _safe(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return html.escape(str(value))


def _status_label(value: str) -> str:
    return {
        "CONFLICT": "충돌",
        "REVIEW_REQUIRED": "확인 필요",
        "NO_CONFLICT": "충돌 없음",
    }.get(value, value)


def _scenario_label(value: str) -> str:
    return {
        "WORKING_CAPITAL_LOAN_MATURITY": "운전자금 대출 만기",
        "FX_FORWARD_MATURITY": "선물환 만기",
        "SUPPLIER_PAYMENT": "공급자 지급",
    }.get(value, value)


def _document_type_label(value: str) -> str:
    return {
        "BOOKING_CONFIRMATION": "Booking",
        "COMMERCIAL_INVOICE": "상업송장",
        "BILL_OF_LADING": "선하증권",
    }.get(value, value)


def _footer(canvas: Any, document: Any, *, font: str) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont(font, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "TradeFlow AI Agent · 근거 기반 분석 보고서")
    canvas.drawRightString(192 * mm, 9 * mm, str(document.page))
    canvas.restoreState()


def _styles(font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PortfolioTitle",
            parent=base["Title"],
            fontName=font,
            fontSize=23,
            leading=31,
            alignment=TA_LEFT,
            textColor=NAVY,
            spaceAfter=4 * mm,
            wordWrap="CJK",
        ),
        "subtitle": ParagraphStyle(
            "PortfolioSubtitle",
            parent=base["BodyText"],
            fontName=font,
            fontSize=9,
            leading=14,
            textColor=MUTED,
            spaceAfter=5 * mm,
            wordWrap="CJK",
        ),
        "heading": ParagraphStyle(
            "PortfolioHeading",
            parent=base["Heading2"],
            fontName=font,
            fontSize=14,
            leading=20,
            textColor=NAVY,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "PortfolioBody",
            parent=base["BodyText"],
            fontName=font,
            fontSize=8.7,
            leading=13.5,
            textColor=colors.HexColor("#243447"),
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "PortfolioSmall",
            parent=base["BodyText"],
            fontName=font,
            fontSize=7.2,
            leading=10.5,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "cell": ParagraphStyle(
            "PortfolioCell",
            parent=base["BodyText"],
            fontName=font,
            fontSize=7.4,
            leading=10.2,
            textColor=colors.HexColor("#243447"),
            wordWrap="CJK",
        ),
        "head": ParagraphStyle(
            "PortfolioHead",
            parent=base["BodyText"],
            fontName=font,
            fontSize=7.5,
            leading=10.2,
            textColor=colors.white,
            wordWrap="CJK",
        ),
    }


def _table(
    rows: list[list[Any]],
    *,
    styles: dict[str, ParagraphStyle],
    widths: list[float],
) -> Table:
    rendered = [
        [
            cell
            if isinstance(cell, Paragraph)
            else Paragraph(_safe(cell), styles["head"] if row_index == 0 else styles["cell"])
            for cell in row
        ]
        for row_index, row in enumerate(rows)
    ]
    result = Table(rendered, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if len(rows) >= 3:
        commands.append(("BACKGROUND", (0, 2), (-1, -1), PALE))
    result.setStyle(TableStyle(commands))
    return result


class PortfolioReportGenerator:
    """Render verified structured results without adding new financial judgement."""

    def generate(
        self,
        payload: PortfolioReportPayload | dict[str, Any],
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        frozen = (
            payload
            if isinstance(payload, PortfolioReportPayload)
            else PortfolioReportPayload.model_validate(payload)
        )
        path = output_path or self._default_path(frozen)
        path.parent.mkdir(parents=True, exist_ok=True)
        font = _register_font()
        styles = _styles(font)
        document = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=19 * mm,
            title="무역금융 AI Agent 분석 보고서",
            author="TradeFlow AI Agent",
        )
        story: list[Any] = [
            Paragraph("무역금융 AI Agent 분석 보고서", styles["title"]),
            Paragraph(
                "Document Agent → Financial Conflict Agent → Product Advisor Agent의 "
                "검증된 결과를 Report Generator가 그대로 구성했습니다.",
                styles["subtitle"],
            ),
            HRFlowable(width="100%", thickness=1.2, color=BLUE, spaceAfter=4 * mm),
            Paragraph("1. 거래·문서 요약", styles["heading"]),
        ]

        document_rows: list[list[Any]] = [["문서", "유형", "핵심 추출값", "검토사항"]]
        for document_item in frozen.documents:
            compact_fields = ", ".join(
                f"{key}={value}"
                for key, value in list(document_item.fields.items())[:8]
            ) or "-"
            document_rows.append(
                [
                    document_item.file_name,
                    _document_type_label(document_item.document_type),
                    compact_fields,
                    (
                        "없음"
                        if document_item.status == "ANALYZED"
                        else ", ".join(document_item.warnings) or "확인 필요"
                    ),
                ]
            )
        story.append(
            _table(
                document_rows,
                styles=styles,
                widths=[40 * mm, 31 * mm, 72 * mm, 31 * mm],
            )
        )

        story.extend(
            [
                Paragraph("2. 금융충돌 분류", styles["heading"]),
                Paragraph(
                    "예상 대금 유입일과 세 종류의 금융일정을 날짜 규칙으로 비교했습니다. "
                    "LLM은 충돌 날짜를 계산하지 않습니다.",
                    styles["body"],
                ),
                Spacer(1, 2 * mm),
            ]
        )
        conflict_rows: list[list[Any]] = [
            ["시나리오", "상태", "금융일", "예상 유입일", "차이", "이유"]
        ]
        for conflict_item in frozen.conflicts:
            conflict_rows.append(
                [
                    _scenario_label(conflict_item.scenario_code),
                    _status_label(conflict_item.status),
                    conflict_item.event_date.isoformat(),
                    conflict_item.expected_receipt_date.isoformat()
                    if conflict_item.expected_receipt_date
                    else "미확정",
                    (
                        f"{conflict_item.gap_days}일"
                        if conflict_item.gap_days is not None
                        else "-"
                    ),
                    conflict_item.reason,
                ]
            )
        story.append(
            _table(
                conflict_rows,
                styles=styles,
                widths=[34 * mm, 24 * mm, 25 * mm, 28 * mm, 18 * mm, 45 * mm],
            )
        )

        story.extend([PageBreak(), Paragraph("3. 검토 가능한 금융상품", styles["heading"])])
        if not frozen.product_options:
            story.append(
                Paragraph(
                    "검색 점수와 출처 조건을 만족한 금융상품 근거가 없어 추천을 보류했습니다.",
                    styles["body"],
                )
            )
        else:
            product_rows: list[list[Any]] = [["충돌", "상품", "검토 이유", "근거"]]
            for option in frozen.product_options:
                citation_text = "<br/>".join(
                    f"{_safe(c.source_file)} p.{c.page}: {_safe(c.excerpt[:150])}"
                    for c in option.citations
                )
                product_rows.append(
                    [
                        _scenario_label(option.scenario_code),
                        option.product_name,
                        option.why_consider,
                        Paragraph(citation_text, styles["cell"]),
                    ]
                )
            story.append(
                _table(
                    product_rows,
                    styles=styles,
                    widths=[33 * mm, 38 * mm, 48 * mm, 55 * mm],
                )
            )

        story.extend(
            [
                Paragraph("4. 출처와 확인사항", styles["heading"]),
                Paragraph(_safe(frozen.source_notice), styles["body"]),
                Spacer(1, 3 * mm),
                Paragraph(
                    "보고서 생성일: "
                    f"{frozen.generated_on.isoformat()} · 원본 문서 수: {len(frozen.documents)} · "
                    f"금융일정 수: {len(frozen.conflicts)} · 근거 있는 상품 수: "
                    f"{len(frozen.product_options)}",
                    styles["small"],
                ),
            ]
        )
        footer = partial(_footer, font=font)
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return {
            "report_path": str(path),
            "sha256": self._hash(path),
            "page_count": len(PdfReader(path).pages),
            "document_count": len(frozen.documents),
            "conflict_count": sum(item.status == "CONFLICT" for item in frozen.conflicts),
            "product_option_count": len(frozen.product_options),
        }

    @staticmethod
    def _default_path(payload: PortfolioReportPayload) -> Path:
        token = hashlib.sha256(
            payload.model_dump_json(exclude_none=True).encode("utf-8")
        ).hexdigest()[:12]
        return DEFAULT_REPORT_DIR / f"tradeflow-analysis-{payload.generated_on}-{token}.pdf"

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
