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
    KeepTogether,
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
    """Render structured results; source checks do not certify generated claims."""

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
                "Supervisor가 연결한 Document·Finance Agent의 검토 결과입니다. "
                "날짜 비교는 규칙으로 수행하고 금융 설명에는 검색한 PDF 근거를 붙였습니다.",
                styles["subtitle"],
            ),
            HRFlowable(width="100%", thickness=1.2, color=BLUE, spaceAfter=4 * mm),
            Paragraph("1. 거래·문서 요약", styles["heading"]),
        ]

        document_rows: list[list[Any]] = [["문서", "유형", "핵심 추출값", "검토사항"]]
        for document_item in frozen.documents:
            compact_fields = (
                ", ".join(f"{key}={value}" for key, value in list(document_item.fields.items())[:8])
                or "-"
            )
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
        if frozen.receipt_resolution:
            story.append(
                Paragraph(
                    _safe("대금 유입일 근거: " + frozen.receipt_resolution.basis), styles["body"]
                )
            )
        for conflict_item in frozen.conflicts:
            conflict_rows.append(
                [
                    _scenario_label(conflict_item.scenario_code),
                    _status_label(conflict_item.status),
                    conflict_item.event_date.isoformat() if conflict_item.event_date else "미확정",
                    conflict_item.expected_receipt_date.isoformat()
                    if conflict_item.expected_receipt_date
                    else "미확정",
                    (f"{conflict_item.gap_days}일" if conflict_item.gap_days is not None else "-"),
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

        story.extend([PageBreak(), Paragraph("3. 상황별 확인 정보·상담 준비", styles["heading"])])
        if frozen.service_cards:
            for card_index, card in enumerate(frozen.service_cards):
                if card_index:
                    story.append(PageBreak())
                story.append(Paragraph(_safe(card.title), styles["heading"]))
                story.append(Paragraph(_safe(card.situation), styles["body"]))
                for option in card.information:
                    story.append(Paragraph(_safe(option.product_name), styles["body"]))
                    story.append(Paragraph(_safe(option.why_consider), styles["small"]))
                    if option.explanation_points:
                        for point in option.explanation_points:
                            point_block = [
                                Paragraph(_safe(point.question or "확인할 내용"), styles["body"]),
                                Paragraph("핵심 설명: " + _safe(point.text), styles["body"]),
                                Paragraph(
                                    "근거 원문: " + _safe(point.supporting_quote), styles["small"]
                                ),
                                Paragraph(
                                    _safe(
                                        "; ".join(
                                            f"{c.source_file} p.{c.page}" for c in point.citations
                                        )
                                    ),
                                    styles["small"],
                                ),
                                Spacer(1, 2 * mm),
                            ]
                            story.append(KeepTogether(point_block))
                    else:
                        story.append(
                            Paragraph(
                                "확인한 원문: " + _safe(option.supporting_quote), styles["small"]
                            )
                        )
                        sources = "; ".join(f"{c.source_file} p.{c.page}" for c in option.citations)
                        story.append(Paragraph(_safe(sources), styles["small"]))
                if card.questions:
                    story.append(
                        Paragraph(
                            "추가 확인 질문 · 아래 질문은 원문 인용이 아닌 서비스 체크리스트입니다.",
                            styles["small"],
                        )
                    )
                    for question in card.questions:
                        story.append(Paragraph(_safe(question), styles["body"]))
                for notice in card.notices:
                    story.append(Paragraph(_safe(notice), styles["small"]))
                story.append(Spacer(1, 3 * mm))
                # Generated explanations can span pages. Keep each point with its
                # own quote, rather than forcing an entire card onto one page.
        elif not frozen.product_options:
            story.append(
                Paragraph(
                    "제공할 상품 근거가 없습니다. 검색 생략·입력 확인 필요·검색 실패 여부는 아래 확인사항과 실행 기록을 확인해 주세요.",
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
                        option.product_name
                        + (
                            f"\n{option.financial_institution}"
                            if option.financial_institution
                            else ""
                        ),
                        (
                            "\n".join(p.text for p in option.explanation_points)
                            or option.why_consider
                        )
                        + "\n확인할 조건: "
                        + "; ".join(option.conditions_to_check),
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

        if frozen.service_cards:
            story.append(PageBreak())
        story.extend(
            [
                Paragraph("4. 출처와 확인사항", styles["heading"]),
                *[Paragraph(_safe(warning), styles["body"]) for warning in frozen.warnings],
                Paragraph(_safe(frozen.source_notice), styles["body"]),
                Paragraph(
                    "설명은 검색한 자료를 바탕으로 AI가 작성했습니다. 출처와 인용문 일치 여부는 확인하지만, "
                    "설명의 의미·조건·예외 및 고객 계약에 대한 적용 여부는 담당자의 검토가 필요합니다.",
                    styles["small"],
                ),
                Spacer(1, 3 * mm),
                Paragraph(
                    "보고서 생성일: "
                    f"{frozen.generated_on.isoformat()} · 원본 문서 수: {len(frozen.documents)} · "
                    f"금융일정 수: {len(frozen.conflicts)} · 거래별 자료 연결 수: "
                    f"{len(frozen.product_options)}",
                    styles["small"],
                ),
            ]
        )
        if any(item.evidence for item in frozen.documents):
            story.append(Paragraph("5. 문서 추출 근거", styles["heading"]))
            for item in frozen.documents:
                for evidence in item.evidence:
                    story.append(
                        Paragraph(
                            _safe(
                                f"{item.file_name} p.{evidence.get('page', '-')} · {evidence.get('field', '-')} · "
                                f"원문: {evidence.get('raw_value', '-')}"
                            ),
                            styles["small"],
                        )
                    )
        if frozen.product_options and not frozen.service_cards:
            story.append(Paragraph("6. 금융자료 발췌 원문", styles["heading"]))
            seen: set[tuple[str, int, str]] = set()
            for option in frozen.product_options:
                for citation in option.citations:
                    identity = (citation.source_file, citation.page, citation.excerpt)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    story.append(
                        Paragraph(
                            _safe(f"{citation.source_file} p.{citation.page}"), styles["small"]
                        )
                    )
                    story.append(Paragraph(_safe(citation.excerpt), styles["body"]))
                    story.append(Spacer(1, 2 * mm))
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
