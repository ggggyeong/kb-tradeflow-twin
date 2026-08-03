from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "output" / "pdf" / "KB_TradeFlow_Twin_Demo_0_1_2_시나리오_가이드.pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4

# KB-inspired presentation palette.  Colors are intentionally restrained so
# the guide is readable both on screen and on office printers.
KB_YELLOW = colors.HexColor("#FFB300")
KB_GOLD = colors.HexColor("#D99A00")
KB_BROWN = colors.HexColor("#3B342D")
KB_NAVY = colors.HexColor("#071F3D")
KB_BLUE = colors.HexColor("#1268B3")
KB_TEAL = colors.HexColor("#008F87")
INK = colors.HexColor("#152238")
MUTED = colors.HexColor("#617084")
LINE = colors.HexColor("#D9E1EA")
PAPER = colors.HexColor("#F5F7FA")
PALE_YELLOW = colors.HexColor("#FFF7DF")
PALE_BLUE = colors.HexColor("#EDF5FC")
PALE_TEAL = colors.HexColor("#EAF8F6")
PALE_RED = colors.HexColor("#FDEEEE")
WHITE = colors.white

FONT_NORMAL = "HYSMyeongJo-Medium"
FONT_BOLD = "HYGothic-Medium"


def _register_fonts() -> None:
    """Register ReportLab's portable Korean CID fonts.

    Using the CID fonts avoids macOS-specific TrueType subsetting problems and
    keeps Hangul visible in PDFium, Preview, and Acrobat.
    """
    registered = set(pdfmetrics.getRegisteredFontNames())
    for font_name in (FONT_NORMAL, FONT_BOLD):
        if font_name not in registered:
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_eyebrow": ParagraphStyle(
            "CoverEyebrow",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=8.4,
            leading=12,
            textColor=KB_YELLOW,
            spaceAfter=4 * mm,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=29,
            leading=37,
            textColor=WHITE,
            spaceAfter=4 * mm,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=12.5,
            leading=19,
            textColor=colors.HexColor("#E6EDF5"),
            spaceAfter=8 * mm,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=8.4,
            leading=13,
            textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName=FONT_BOLD,
            fontSize=20,
            leading=27,
            textColor=KB_NAVY,
            spaceBefore=2 * mm,
            spaceAfter=5 * mm,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=13.5,
            leading=19,
            textColor=KB_BLUE,
            spaceBefore=5 * mm,
            spaceAfter=2.5 * mm,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName=FONT_BOLD,
            fontSize=10.5,
            leading=15,
            textColor=KB_BROWN,
            spaceBefore=3.5 * mm,
            spaceAfter=1.5 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=9.2,
            leading=14.6,
            textColor=INK,
            spaceAfter=2.2 * mm,
        ),
        "body_compact": ParagraphStyle(
            "BodyCompact",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=8.4,
            leading=12.8,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=7.4,
            leading=10.8,
            textColor=MUTED,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=7.2,
            leading=10.5,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=2 * mm,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=7.7,
            leading=10.5,
            textColor=WHITE,
            alignment=TA_LEFT,
        ),
        "table_body": ParagraphStyle(
            "TableBody",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=7.5,
            leading=10.9,
            textColor=INK,
        ),
        "table_body_small": ParagraphStyle(
            "TableBodySmall",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=6.8,
            leading=9.5,
            textColor=INK,
        ),
        "callout_title": ParagraphStyle(
            "CalloutTitle",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=8.8,
            leading=12.8,
            textColor=KB_NAVY,
        ),
        "callout_body": ParagraphStyle(
            "CalloutBody",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=8.2,
            leading=12.7,
            textColor=INK,
        ),
        "flow": ParagraphStyle(
            "Flow",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=8.1,
            leading=13.2,
            alignment=TA_CENTER,
            textColor=KB_NAVY,
        ),
        "dialogue_user": ParagraphStyle(
            "DialogueUser",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=8.4,
            leading=13,
            textColor=WHITE,
        ),
        "dialogue_agent": ParagraphStyle(
            "DialogueAgent",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=8.4,
            leading=13,
            textColor=INK,
        ),
        "toc_number": ParagraphStyle(
            "TocNumber",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=10,
            leading=14,
            textColor=KB_GOLD,
            alignment=TA_CENTER,
        ),
        "toc_text": ParagraphStyle(
            "TocText",
            parent=base["BodyText"],
            fontName=FONT_NORMAL,
            fontSize=9,
            leading=14,
            textColor=INK,
        ),
    }


def _escape(value: Any) -> str:
    return html.escape(str(value)).replace("\n", "<br/>")


def _p(value: Any, style: ParagraphStyle, *, markup: bool = False) -> Paragraph:
    text = str(value) if markup else _escape(value)
    return Paragraph(text, style)


def _table(
    rows: Sequence[Sequence[Any]],
    widths: Sequence[float],
    styles: dict[str, ParagraphStyle],
    *,
    header_color: colors.Color = KB_NAVY,
    small: bool = False,
    repeat_rows: int = 1,
) -> Table:
    body_style = styles["table_body_small"] if small else styles["table_body"]
    prepared: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        row_style = styles["table_header"] if row_index < repeat_rows else body_style
        prepared.append(
            [
                cell if isinstance(cell, (Paragraph, Table)) else _p(cell, row_style, markup=False)
                for cell in row
            ]
        )
    table = Table(
        prepared,
        colWidths=list(widths),
        repeatRows=repeat_rows,
        hAlign="LEFT",
        splitByRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, repeat_rows - 1), header_color),
                ("TEXTCOLOR", (0, 0), (-1, repeat_rows - 1), WHITE),
                ("BACKGROUND", (0, repeat_rows), (-1, -1), WHITE),
                ("ROWBACKGROUNDS", (0, repeat_rows), (-1, -1), [WHITE, PAPER]),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _callout(
    title: str,
    body: str,
    styles: dict[str, ParagraphStyle],
    *,
    background: colors.Color = PALE_BLUE,
    accent: colors.Color = KB_BLUE,
) -> Table:
    content = [
        _p(title, styles["callout_title"]),
        Spacer(1, 1.2 * mm),
        _p(body, styles["callout_body"]),
    ]
    inner = Table([[content]], colWidths=[166 * mm])
    inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return inner


def _dialogue(
    speaker: str,
    text: str,
    styles: dict[str, ParagraphStyle],
    *,
    user: bool = False,
) -> Table:
    label = "사용자" if user else "KB TradeFlow Twin"
    label_style = styles["small"]
    body_style = styles["dialogue_user"] if user else styles["dialogue_agent"]
    bubble = Table(
        [[_p(label, label_style)], [_p(text, body_style)]],
        colWidths=[130 * mm],
        hAlign="RIGHT" if user else "LEFT",
    )
    bubble.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 1), (0, 1), KB_BLUE if user else colors.HexColor("#ECEAE6")),
                ("BOX", (0, 1), (0, 1), 0.3, KB_BLUE if user else LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return bubble


def _choice_buttons(
    labels: Sequence[str],
    styles: dict[str, ParagraphStyle],
) -> Table:
    cells = [_p(label, styles["flow"]) for label in labels]
    width = 166 * mm / max(len(cells), 1)
    table = Table([cells], colWidths=[width] * len(cells), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WHITE),
                ("BOX", (0, 0), (-1, -1), 0.8, KB_BLUE),
                ("INNERGRID", (0, 0), (-1, -1), 0.8, KB_BLUE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _flowline(text: str, styles: dict[str, ParagraphStyle]) -> Table:
    flow = Table([[_p(text, styles["flow"])]], colWidths=[166 * mm])
    flow.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
                ("BOX", (0, 0), (-1, -1), 0.7, KB_TEAL),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return flow


def _checklist(
    items: Iterable[str],
    styles: dict[str, ParagraphStyle],
) -> Table:
    rows = [["□", item] for item in items]
    prepared = [
        [_p(mark, styles["table_body"]), _p(text, styles["table_body"])] for mark, text in rows
    ]
    table = Table(prepared, colWidths=[9 * mm, 157 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, PAPER]),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _draw_cover(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setFillColor(KB_NAVY)
    canvas.rect(0, PAGE_HEIGHT - 122 * mm, PAGE_WIDTH, 122 * mm, fill=1, stroke=0)
    canvas.setFillColor(KB_BROWN)
    canvas.rect(0, 0, PAGE_WIDTH, 13 * mm, fill=1, stroke=0)
    canvas.setFillColor(KB_YELLOW)
    canvas.rect(0, PAGE_HEIGHT - 124.2 * mm, PAGE_WIDTH, 2.2 * mm, fill=1, stroke=0)
    # Abstract KB Twin mark: two linked operational lanes.
    canvas.setStrokeColor(KB_YELLOW)
    canvas.setLineWidth(3.5)
    canvas.line(166 * mm, PAGE_HEIGHT - 31 * mm, 188 * mm, PAGE_HEIGHT - 31 * mm)
    canvas.line(166 * mm, PAGE_HEIGHT - 39 * mm, 188 * mm, PAGE_HEIGHT - 39 * mm)
    canvas.line(177 * mm, PAGE_HEIGHT - 23 * mm, 177 * mm, PAGE_HEIGHT - 47 * mm)
    canvas.setFillColor(WHITE)
    canvas.setFont(FONT_BOLD, 9)
    canvas.drawString(17 * mm, 7 * mm, "KB TradeFlow Twin · Final Demo Runbook")
    canvas.restoreState()


def _draw_page(canvas: Any, document: Any) -> None:
    if document.page == 1:
        _draw_cover(canvas, document)
        return
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.45)
    canvas.line(17 * mm, PAGE_HEIGHT - 13 * mm, 193 * mm, PAGE_HEIGHT - 13 * mm)
    canvas.setFont(FONT_NORMAL, 7.4)
    canvas.setFillColor(MUTED)
    canvas.drawString(17 * mm, PAGE_HEIGHT - 9.2 * mm, "KB TradeFlow Twin")
    canvas.drawRightString(193 * mm, PAGE_HEIGHT - 9.2 * mm, "Demo 0·1·2 시나리오 가이드")
    canvas.line(17 * mm, 13 * mm, 193 * mm, 13 * mm)
    canvas.drawString(17 * mm, 8.2 * mm, "공모전 시연용 · 합성 데이터 · 외부 API 미사용 문서")
    canvas.drawRightString(193 * mm, 8.2 * mm, f"{document.page}")
    canvas.restoreState()


def _add_section_title(
    story: list[Any],
    number: str,
    title: str,
    styles: dict[str, ParagraphStyle],
    subtitle: str | None = None,
) -> None:
    story.append(_p(f"{number}. {title}", styles["h1"]))
    if subtitle:
        story.append(_p(subtitle, styles["body"]))


def _build_story(styles: dict[str, ParagraphStyle]) -> list[Any]:
    story: list[Any] = []

    # Cover
    story.extend(
        [
            Spacer(1, 24 * mm),
            _p("MULTI-AGENT TRADE ASSISTANT", styles["cover_eyebrow"]),
            _p("KB TradeFlow Twin", styles["cover_title"]),
            _p(
                "Demo 0·1·2 최종 시나리오 가이드<br/>"
                "문서 등록부터 금융위험 분석, 근거 기반 상품 안내, 고객·RM 보고서까지",
                styles["cover_subtitle"],
                markup=True,
            ),
            Spacer(1, 55 * mm),
            _callout(
                "시연 원칙",
                "결과 화면만 보여주는 데모가 아니다. 왼쪽 Streamlit Chat에서 사용자가 선택하고 답변하며, "
                "오른쪽 LangGraph Studio에서 Planning → Supervisor → 전문 Agent → Tool → Critic의 실제 실행 순서를 "
                "동일 Thread로 관찰한다.",
                styles,
                background=PALE_YELLOW,
                accent=KB_GOLD,
            ),
            Spacer(1, 7 * mm),
            _p(
                "범위: Demo 0 문서 분류·거래 매칭 / Demo 1 금일 선제 위험 분석 / "
                "Demo 2 사용자 제보 지연 분석 / Human interrupt·resume / 상품 RAG / 고객·RM PDF / 상담사 연결 UI",
                styles["cover_meta"],
            ),
            _p(
                "기준 데이터: 공모전용 합성 거래 · 금융정보는 별도 2-Sheet XLSX · 상담사 연결은 화면 확인까지만 구현",
                styles["cover_meta"],
            ),
            PageBreak(),
        ]
    )

    # Contents and executive map
    _add_section_title(
        story,
        "0",
        "세 데모가 하나의 이야기로 이어지는 방식",
        styles,
        "세 데모는 서로 다른 기능 쇼케이스가 아니라, 한 기업의 무역 거래가 데이터가 되고 위험 대응으로 이어지는 전체 여정이다.",
    )
    story.append(
        _table(
            [
                ["데모", "심사위원이 보는 문제", "핵심 멀티 Agent 협업", "끝나는 화면"],
                [
                    "Demo 0",
                    "섞여 들어온 Booking·Invoice·B/L을 어떻게 거래로 이해하는가",
                    "Document Intelligence → Trade Case Manager → Critic",
                    "거래 1건 DB 반영 및 금융일정 등록 제안",
                ],
                [
                    "Demo 1",
                    "사전 통보 없이 B/L을 기다릴 때 오늘 위험을 어떻게 먼저 찾는가",
                    "Financial Calendar → Shipment Timeline → Financial Exposure → Critic → Report Writer",
                    "금일 위험 요약, 고객·RM PDF, 상담사 연결 UI",
                ],
                [
                    "Demo 2",
                    "선적 지연을 사전 통보받았을 때 미래 충돌을 어떻게 계산하는가",
                    "Shipment Timeline → Financial Exposure → Human → Product Advisor → Report Writer → Critic",
                    "조건부 충돌 요약, 고객·RM PDF, 상담사 연결 UI",
                ],
            ],
            [19 * mm, 51 * mm, 61 * mm, 35 * mm],
            styles,
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        _flowline(
            "왼쪽 Streamlit 질문·선택 → :2024 chat graph 실행 → 오른쪽 Studio 동일 Thread 관찰 → "
            "Human 응답으로 resume → 최종 답변과 PDF는 왼쪽에 표시",
            styles,
        )
    )
    story.append(_p("목차", styles["h2"]))
    toc_rows = [
        ["01", "공통 사전 준비와 파일 배치"],
        ["02", "Demo 0 - 문서 분류·거래 매칭"],
        ["03", "Demo 1 - 금일 금융위험 선제 분석"],
        ["04", "Demo 2 - 사용자 제보 선적 지연 분석"],
        ["05", "Human interrupt / resume"],
        ["06", "상품 RAG와 고객·RM 보고서"],
        ["07", "DB 증거와 심사위원 설명법"],
        ["08", "화면별 최종 리허설 체크리스트"],
    ]
    toc = Table(
        [[_p(row[0], styles["toc_number"]), _p(row[1], styles["toc_text"])] for row in toc_rows],
        colWidths=[18 * mm, 148 * mm],
        hAlign="LEFT",
    )
    toc.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, PAPER]),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([toc, PageBreak()])

    # Shared materials
    _add_section_title(
        story,
        "1",
        "공통 사전 준비와 파일 배치",
        styles,
        "PDF와 금융일정 XLSX는 서로 다른 Batch로 올린다. 모든 식별자는 같은 로그인 회사와 기존 TradeCase 소유권을 가리켜야 한다.",
    )
    story.append(_p("1.1 권장 자료 디렉터리", styles["h2"]))
    story.append(
        _table(
            [
                ["폴더", "필수 파일", "목적"],
                [
                    "data/judge_demo_final/demo_0_complete_batch",
                    "Booking 1.pdf / Invoice 1.pdf / BL 1.pdf / manifest.json",
                    "3종 문서 분류, 한 거래 매칭, DB commit",
                ],
                [
                    "data/judge_demo_final/demo_1_manual_today_risk",
                    "Booking.pdf / Invoice.pdf / financial_calendar_seed.json / scenario.json",
                    "B/L 미수령 상태에서 금일 선제 위험 분석",
                ],
                [
                    "data/judge_demo_final/demo_2_reported_delay",
                    "Booking.pdf / Invoice.pdf / financial_calendar_seed.json / scenario.json",
                    "사용자 제보 9일 지연의 조건부 충돌 분석",
                ],
                [
                    "data/knowledge",
                    "KB 금융상품 원문 PDF / OCR page index / source SHA manifest",
                    "상품 후보를 페이지 근거와 함께 제시",
                ],
            ],
            [43 * mm, 70 * mm, 53 * mm],
            styles,
        )
    )
    story.append(_p("1.2 공통 실행 환경", styles["h2"]))
    story.append(
        _checklist(
            [
                "LangGraph Server가 :2024에서 실행되고 chat graph가 로드되어 있다.",
                "Streamlit이 :2024에 Thread를 생성하고 같은 thread_id를 세션 동안 보관한다.",
                "오른쪽 Studio에서 같은 Thread를 선택한 뒤 Graph 화면을 연다.",
                "PDF 업로드 Batch와 XLSX 업로드 Batch를 분리한다.",
                "로그인 회사 company_id가 TradeCase·FinancialEvent·Link의 소유 회사와 일치한다.",
                "상품 PDF와 로컬 OCR 인덱스의 SHA가 일치한다.",
                "Demo 1·2의 Company 상품 프로필은 borrower_type=CORPORATION이며, 적용하지 않을 상품 hard requirement는 사전에 명시되어 있다.",
                "고객용·RM용 보고서 출력 폴더가 쓰기 가능하다.",
                "API 비용 보호를 위해 리허설은 합성 데이터와 최소 응답 길이로 실행한다.",
            ],
            styles,
        )
    )
    story.append(_p("1.3 금융일정 2-Sheet 최소 계약", styles["h2"]))
    story.append(
        _table(
            [
                ["Sheet", "필수 컬럼", "원칙"],
                [
                    "1.금융이벤트",
                    "event_id, event_type_code, event_date, amount, currency, financial_institution",
                    "회사 ID는 로그인 세션에서 주입. 회사가 보유한 확정 금융 이벤트만 입력",
                ],
                [
                    "2.거래연결",
                    "transaction_id, event_id, link_type, link_status",
                    "기존 TradeCase의 transaction_id만 허용. dependency_scope 등은 충돌 후 필요할 때 질문",
                ],
            ],
            [28 * mm, 83 * mm, 55 * mm],
            styles,
        )
    )
    story.append(
        _callout(
            "날짜 고정 원칙",
            "Demo 1의 '금일'은 서버 실제 날짜에 맡기지 않는다. 리허설과 본 시연에서 동일한 결과를 얻도록 "
            "데모 기준일을 2026-08-20으로 고정한다. scenario.json과 금융 이벤트 날짜도 이 기준을 따른다.",
            styles,
            background=PALE_RED,
            accent=colors.HexColor("#C94B4B"),
        )
    )
    story.append(PageBreak())

    # Demo 0
    _add_section_title(
        story,
        "2",
        "Demo 0 - 문서 분류·거래 매칭",
        styles,
        "한 번에 들어온 세 PDF가 무엇인지 식별하고, 같은 거래로 묶고, 원천 사실과 운영용 TradeCase·Shipment를 DB에 반영한다.",
    )
    story.append(_p("2.1 사전 자료와 기대값", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "데모 값", "검증 포인트"],
                ["회사", "DEMO0-CO / HANBIT PRECISION", "로그인 회사와 일치"],
                [
                    "거래 식별",
                    "CASE-DEMO0 / TXN-DEMO0",
                    "Booking 번호 기반 case_id와 금융 transaction_id",
                ],
                ["Booking", "BK-DEMO0-0001", "shipper, 항구, 선박, 항차, ETD 포함"],
                [
                    "Invoice",
                    "INV-DEMO0-0001 / USD 40,000",
                    "seller, buyer, goods, PaymentTerms 포함",
                ],
                [
                    "B/L",
                    "BL-DEMO0-0001",
                    "shipper, consignee, 항구, 선박, 항차, on-board date 포함",
                ],
                ["예상 결과", "문서 3건 → 거래 후보 1건 → commit 1건", "누락 없음, Critic PASS"],
            ],
            [31 * mm, 57 * mm, 78 * mm],
            styles,
        )
    )
    story.append(_p("2.2 Graph와 Tool 실행 순서", styles["h2"]))
    story.append(
        _flowline(
            "Planning → Supervisor → Document Intelligence(stage_document_intelligence) → Planning → "
            "Supervisor → Trade Case Manager(bundle_trade_cases → commit_trade_cases) → "
            "Critic(review_workflow_evidence) → Planning 최종 응답",
            styles,
        )
    )
    story.append(
        _table(
            [
                ["순서", "Agent / Tool", "실제 하는 일", "DB 증거"],
                [
                    "1",
                    "Document Intelligence\nstage_document_intelligence",
                    "PDF 고정 신호로 Booking·Invoice·B/L 분류, 종류별 필드 추출·정규화, 필수 누락 계산",
                    "IngestionBatch, Document, DocumentFact",
                ],
                [
                    "2",
                    "Trade Case Manager\nbundle_trade_cases",
                    "Transaction Reference가 없어 seller/shipper·goods/commodity·B/L No·항구·선박·항차 점수로 결정론적 매칭",
                    "분석 envelope의 거래 proposal",
                ],
                [
                    "3",
                    "Trade Case Manager\ncommit_trade_cases",
                    "Company·TradeCase upsert, Shipment planned/actual projection, 원천 문서 연결, PaymentTerms 결정론 파싱",
                    "Company, TradeCase, Shipment, PaymentObligation",
                ],
                [
                    "4",
                    "Independent Critic\nreview_workflow_evidence",
                    "task 결과·문서 수·거래 수·commit 결과와 before/after 불변성 검증",
                    "task evidence 및 TraceEvent",
                ],
            ],
            [11 * mm, 45 * mm, 72 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("2.3 화면별 진행 대사", styles["h2"]))
    story.append(_dialogue("사용자", "📄 새로운 무역파일 등록", styles, user=True))
    story.append(Spacer(1, 2 * mm))
    story.append(
        _dialogue(
            "agent",
            "Booking, Commercial Invoice, B/L PDF를 선택해 주세요. 문서를 분류하고 거래별로 묶은 뒤 필수 항목을 확인하겠습니다.",
            styles,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(_choice_buttons(["PDF 3개 선택", "업로드하고 분석"], styles))
    story.append(Spacer(1, 2 * mm))
    story.append(
        _dialogue(
            "agent",
            "문서 분석을 완료했습니다. Booking 1건, Invoice 1건, B/L 1건을 CASE-DEMO0 거래로 매칭했습니다. "
            "필수 누락은 없으며 TradeCase 1건과 Shipment 1건을 DB에 반영했습니다. Critic 판정은 PASS입니다.",
            styles,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        _callout(
            "다음 선택",
            "금융결제 조건이 있으세요?  [2-Sheet 금융일정 등록하기]  [나중에]",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )
    story.append(_p("2.4 Human 질문의 경계", styles["h2"]))
    story.append(
        _callout(
            "Demo 0 본 경로에는 질문이 없다",
            "세 문서와 Core22가 모두 완전하므로 Human interrupt 없이 commit한다. 일반 운영에서 문서는 있으나 핵심 필드만 비어 있으면 "
            "apply_document_field_override 질문을 만들 수 있고, B/L 문서 전체가 없으면 값을 지어내지 않고 AWAITING_DOCUMENT로 계속 관리한다.",
            styles,
            background=PALE_TEAL,
            accent=KB_TEAL,
        )
    )
    story.append(PageBreak())

    # Demo 1
    _add_section_title(
        story,
        "3",
        "Demo 1 - 금일 금융위험 선제 분석",
        styles,
        "선사가 지연을 미리 알려주지 않은 상황이다. B/L을 기다리고 있는 거래를 시스템이 오늘 기준으로 먼저 찾아 위험을 보고한다.",
    )
    story.append(_p("3.1 사전 자료와 계산 가능한 기대값", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "값", "해석"],
                ["기준일", "2026-08-20", "Chat의 '금일' 기준일"],
                ["거래", "CASE-DEMO1 / TXN-DEMO1", "Booking·Invoice 저장, B/L 없음"],
                ["Booking ETD", "2026-08-05", "Shipment 계획 기준"],
                ["Invoice", "USD 25,000", "예상 수출대금"],
                ["PaymentTerms", "T/T 60 DAYS AFTER ON-BOARD DATE", "On-board date + 60일"],
                [
                    "금융 이벤트",
                    "2026-10-10 / 운전자금대출 만기 / KRW 30,000,000",
                    "거래 회수대금과 연결",
                ],
                ["안전 한계", "최종 On-board 기준일 2026-08-11", "8/11 + 60일 = 10/10"],
                ["금일 판단", "안전 한계 9일 경과", "8/20 기준 더 지연되면 만기 전에 회수 불가"],
                [
                    "예상 결과",
                    "PREEMPTIVE_BREACH / ACTION_REQUIRED",
                    "현금 유입보다 대출 만기가 먼저 도래할 위험",
                ],
            ],
            [32 * mm, 60 * mm, 74 * mm],
            styles,
        )
    )
    story.append(
        _callout(
            "심사위원에게 말할 한 문장",
            "B/L이 아직 없다는 사실만 보고하는 것이 아니라, 그 B/L이 언제까지 와야 금융일정을 지킬 수 있었는지 역산하고 오늘 이미 위험선에 들어왔음을 먼저 알려줍니다.",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )
    story.append(_p("3.2 Graph와 Tool 실행 순서", styles["h2"]))
    story.append(
        _flowline(
            "Planning → Financial Calendar(select_financial_monitoring_candidates) → "
            "Shipment Timeline(read_shipment_snapshot) → Financial Exposure(run_proactive_risk_scan) → "
            "Critic(review_workflow_evidence) → Report Writer(finalize_manual_monitoring) → Planning",
            styles,
        )
    )
    story.append(
        _table(
            [
                ["순서", "Agent / Tool", "처리", "저장 또는 반환"],
                [
                    "1",
                    "Financial Calendar\nselect_financial_monitoring_candidates",
                    "로그인 회사, monitoring_enabled, transaction_id, FinancialEvent link가 있는 Case만 선택",
                    "candidate case 목록",
                ],
                [
                    "2",
                    "Shipment Timeline\nread_shipment_snapshot",
                    "Booking ETD와 B/L·on-board 미확정 상태를 읽어 현재 shipment 사실 고정",
                    "Shipment snapshot",
                ],
                [
                    "3",
                    "Financial Exposure\nrun_proactive_risk_scan",
                    "Payment Gate, 금융 이벤트·link 조회, 안전 기준일 frontier 및 충돌·우선순위 계산",
                    "CalculationResult(MONITORING_SCAN), Conflict",
                ],
                [
                    "4",
                    "Critic\nreview_workflow_evidence",
                    "모든 후보별 결과 존재, 숫자 근거, 상품·보고서 전 단계 계약을 독립 검수",
                    "PASS / REPLAN evidence",
                ],
                [
                    "5",
                    "Report Writer\nfinalize_manual_monitoring",
                    "PASS 결과만 P1→P2로 정렬하고 수동 Chat 실행으로 MonitoringRun·Alert 저장",
                    "MonitoringRun, Alert, 내부 금일 PDF",
                ],
            ],
            [11 * mm, 46 * mm, 71 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("3.3 화면별 진행 대사", styles["h2"]))
    story.append(_dialogue("사용자", "⚡ 금일 금융위험 거래 분석", styles, user=True))
    story.append(Spacer(1, 2 * mm))
    story.append(
        _dialogue(
            "agent",
            "2026-08-20 기준으로 분석 가능한 거래를 조회하고, 선적 상태와 금융일정의 충돌 위험을 계산하겠습니다.",
            styles,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        _dialogue(
            "agent",
            "금일 금융위험 분석을 완료했습니다. CASE-DEMO1은 B/L 미수령 상태이며 안전 기준일 2026-08-11을 9일 경과했습니다. "
            "운전자금대출 만기 2026-10-10 전에 수출대금을 회수하지 못할 선제 위험이 발견되어 ACTION_REQUIRED로 분류했습니다.",
            styles,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        _callout(
            "후속 선택",
            "충돌 종합 보고서를 만들어 드릴까요?  [고객용·RM용 종합 보고서 생성]  [나중에]",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )
    story.append(_p("3.4 성공 판정", styles["h2"]))
    story.append(
        _checklist(
            [
                "Studio에서 후보 선택 후 Shipment와 Financial Agent가 순서대로 실행된다.",
                "Chat 결과에 기준일, 거래 ID, B/L 상태, 금융 이벤트, gap, 우선순위가 모두 보인다.",
                "CalculationResult.source_kind가 MONITORING_SCAN이다.",
                "Conflict 1건과 P1 우선순위가 생성된다.",
                "MonitoringRun은 execution_source=CHAT_MANUAL 흐름으로 저장된다.",
                "중복 실행 시 같은 신호의 Alert가 중복 생성되지 않는다.",
                "보고서 생성 버튼이 위험 요약 아래에 노출된다.",
            ],
            styles,
        )
    )
    story.append(PageBreak())

    # Demo 2
    _add_section_title(
        story,
        "4",
        "Demo 2 - 사용자 제보 선적 지연 분석",
        styles,
        "선사가 지연을 미리 알려준 상황이다. 사용자가 거래 ID와 지연 일수를 말하면 실제 운항 사실을 덮어쓰지 않고 조건부 금융영향을 계산한다.",
    )
    story.append(_p("4.1 사전 자료와 계산 가능한 기대값", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "값", "해석"],
                ["제보일", "2026-08-20", "사용자 제보 기준"],
                ["거래", "CASE-DEMO2 / TXN-DEMO2", "Booking·Invoice 저장, B/L 없음"],
                ["계획 B/L proxy", "2026-08-15", "Booking ETD를 계획 anchor proxy로 사용"],
                ["사용자 제보", "9일 지연", "조건부 revised expected departure 2026-08-24"],
                [
                    "PaymentTerms",
                    "T/T 45 DAYS AFTER ON-BOARD DATE",
                    "On-board date 적용 확인 후 계산",
                ],
                ["계획 회수일", "2026-09-29", "2026-08-15 + 45일"],
                ["지연 후 회수일", "2026-10-08", "2026-08-24 + 45일"],
                [
                    "금융 이벤트",
                    "2026-10-03 / 공급업체 지급 / KRW 45,000,000",
                    "거래 회수대금 FULL 의존",
                ],
                ["예상 결과", "5일 gap / 충돌 1건 / P1", "지연 후 회수일이 지급일보다 5일 늦음"],
            ],
            [32 * mm, 60 * mm, 74 * mm],
            styles,
        )
    )
    story.append(_p("4.2 사용자가 입력할 정확한 문장", styles["h2"]))
    story.append(
        _dialogue(
            "사용자",
            "CASE-DEMO2 거래의 B/L이 계획보다 9일 늦어진다는 소식을 들었어. 금융일정 충돌과 위험순위를 분석해줘.",
            styles,
            user=True,
        )
    )
    story.append(
        _callout(
            "문장 작성 규칙",
            "case_id(CASE-* 또는 TRD-*), B/L 또는 선적 용어, '지연' 표현, 정수형 일수를 모두 포함한다. TXN-*는 금융 연결 ID이므로 화면 대본에는 CASE-DEMO2를 사용한다.",
            styles,
            background=PALE_RED,
            accent=colors.HexColor("#C94B4B"),
        )
    )
    story.append(_p("4.3 Graph와 Tool 실행 순서", styles["h2"]))
    story.append(
        _flowline(
            "Planning → Shipment Timeline(record_shipment_delay_scenarios) → "
            "Financial Exposure(inspect_payment_gate) → 필요 시 Human → "
            "Financial Exposure(calculate_financial_exposure) → 필요 시 Financial Calendar link 보완 → "
            "Critic(review_workflow_evidence) → Planning",
            styles,
        )
    )
    story.append(
        _table(
            [
                ["순서", "Agent / Tool", "처리", "DB 증거"],
                [
                    "1",
                    "Shipment Timeline\nrecord_shipment_delay_scenarios",
                    "TradeCase·Shipment·Booking ETD 확인, 제보를 ShipmentEvent로 저장, ETD+9일 조건부 시나리오 생성",
                    "ShipmentEvent(USER_REPORTED), CalculationResult scenario",
                ],
                [
                    "2",
                    "Financial Exposure\ninspect_payment_gate",
                    "첫 PaymentObligation의 anchor, tenor, day type, verified, calculation_allowed 검사",
                    "Payment Gate 결과 또는 HumanIssue",
                ],
                [
                    "3",
                    "Financial Exposure\ncalculate_financial_exposure",
                    "Shipment 시나리오, 금융 이벤트·link를 결합해 계획/변경 회수일, 충돌, P1/P2 우선순위 계산",
                    "CalculationResult(USER_REPORTED_DELAY), Conflict",
                ],
                [
                    "4",
                    "Financial Calendar\nupdate_financial_event_link_details",
                    "충돌 후 필요한 dependency_scope 또는 일부 연결금액을 Human 답변으로 명시",
                    "TransactionFinancialEventLink 갱신",
                ],
                [
                    "5",
                    "Critic\nreview_workflow_evidence",
                    "Shipment actual state 불변, 재계산 basis, 충돌 결과, 보고서 동일 basis를 검수",
                    "PASS / REPLAN evidence",
                ],
            ],
            [11 * mm, 47 * mm, 70 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("4.4 중간 선택과 정확한 답변", styles["h2"]))
    story.append(_dialogue("agent", "지급조건의 B/L DATE를 On-board date로 적용할까요?", styles))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["On-board date 적용", "취소"], styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(
        _dialogue(
            "agent",
            "금융 이벤트 EVT-DEMO2-SUPPLIER가 TXN-DEMO2 회수대금에 전액 또는 일부 의존하는지 확인해 주세요.",
            styles,
        )
    )
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["전액", "일부", "취소"], styles))
    story.append(Spacer(1, 2 * mm))
    story.append(
        _callout(
            "본 시연 권장 선택",
            "On-board date 적용 → 전액. 이 경로는 계산 Gate와 거래-금융일정 의존성을 모두 보여주면서 추가 금액 입력 없이 가장 짧게 결과까지 간다. "
            "'일부'를 선택하면 '30000 USD' 형식의 연결 금액·통화 질문이 한 번 더 나온다.",
            styles,
            background=PALE_TEAL,
            accent=KB_TEAL,
        )
    )
    story.append(_p("4.5 예상 채팅 결과", styles["h2"]))
    story.append(
        _dialogue(
            "agent",
            "CASE-DEMO2의 9일 지연 시나리오를 계산했습니다. 계획 회수일은 2026-09-29, 지연 후 회수일은 2026-10-08입니다. "
            "2026-10-03 공급업체 지급보다 5일 늦어 충돌 1건이 발생하며 최고 우선순위는 P1입니다. "
            "실제 B/L·출항 사실은 변경하지 않고 조건부 시나리오로 저장했습니다.",
            styles,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        _callout(
            "후속 선택",
            "충돌 종합 보고서를 만들어 드릴까요?  [고객용·RM용 종합 보고서 생성]  [나중에]",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )
    story.append(PageBreak())

    # Human mechanism
    _add_section_title(
        story,
        "5",
        "Human interrupt / resume",
        styles,
        "Human 질문은 일반 대화 메시지가 아니라 어떤 필드에 어떤 형식의 답이 필요한지 고정한 typed 실행 계약이다.",
    )
    story.append(_p("5.1 상태 전이", styles["h2"]))
    story.append(
        _table(
            [
                ["단계", "노드·저장", "무슨 일이 일어나는가", "다음 상태"],
                [
                    "1",
                    "전문 Agent 결과",
                    "누락 필드·anchor·dependency_scope 등 계산에 필요한 값이 없음을 반환",
                    "Supervisor가 HumanIssue 생성",
                ],
                [
                    "2",
                    "human_question",
                    "issue_code, response_key, value_type, allowed_values 검증",
                    "Confirmation=PENDING",
                ],
                [
                    "3",
                    "human_confirmation",
                    "LangGraph interrupt payload를 UI에 반환하고 실행 중지",
                    "Thread checkpoint 유지",
                ],
                ["4", "Streamlit 응답", "같은 thread_id와 issue_code로 resume", "typed value 검증"],
                [
                    "5",
                    "Confirmation update",
                    "응답이면 ANSWERED, 취소면 CANCELLED, response_json 저장",
                    "human_context 갱신",
                ],
                [
                    "6",
                    "Planning",
                    "남은 plan과 past_steps를 다시 보고 후속 Tool task 수행",
                    "override·link update·재계산",
                ],
            ],
            [10 * mm, 36 * mm, 84 * mm, 36 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("5.2 데모별 Human 질문", styles["h2"]))
    story.append(
        _table(
            [
                ["데모", "질문", "답변", "후속 Tool"],
                [
                    "일반 문서 보완",
                    "문서는 있으나 Core22 한 필드가 비어 있습니다.",
                    "검토된 값",
                    "apply_document_field_override",
                ],
                [
                    "Demo 2 필수",
                    "B/L DATE를 On-board date로 적용할까요?",
                    "On-board date 적용",
                    "inspect gate 갱신 후 calculate",
                ],
                [
                    "Demo 2 권장",
                    "금융 이벤트가 회수대금에 전액/일부 의존합니까?",
                    "전액",
                    "update_financial_event_link_details → 재계산",
                ],
                [
                    "보고서 공통",
                    "고객용·RM용 보고서 생성에 동의합니까?",
                    "동의",
                    "build_briefing_payload → render 2종",
                ],
                [
                    "상품 fallback",
                    "사전 프로필이 없을 때만 사업자 유형 또는 상품 필수요건 확인",
                    "법인사업자 / 사실에 맞는 예·아니오",
                    "match_product_scenario 재실행",
                ],
            ],
            [22 * mm, 66 * mm, 38 * mm, 40 * mm],
            styles,
            small=True,
        )
    )
    story.append(
        _callout(
            "중요",
            "사용자 답변이 저장되는 순간 기존 실행을 처음부터 다시 시작하는 것이 아니다. LangGraph checkpoint에 남아 있던 같은 Thread를 resume하고, "
            "Confirmation 응답을 human_context에 넣은 뒤 Planning이 남은 task만 이어서 수행한다.",
            styles,
            background=PALE_TEAL,
            accent=KB_TEAL,
        )
    )
    story.append(PageBreak())

    # Product / reports
    _add_section_title(
        story,
        "6",
        "상품 RAG와 고객·RM 보고서",
        styles,
        "사용자가 위험 상황을 다시 설명하지 않는다. Financial Exposure 결과가 Product Advisor의 입력이 되고, 상품 원문 PDF의 페이지 근거가 있는 후보만 보고서에 들어간다.",
    )
    story.append(_p("6.1 전체 실행 순서", styles["h2"]))
    story.append(
        _flowline(
            "get_financial_risk_snapshot → match_product_scenario(사전 프로필 hard filter) → "
            "retrieve_product_evidence → 보고서 동의 → build_briefing_payload → "
            "render_customer_report → render_rm_report → review_workflow_evidence",
            styles,
        )
    )
    story.append(
        _table(
            [
                ["단계", "핵심 처리", "검증 가능한 결과"],
                [
                    "위험 snapshot",
                    "MONITORING_SCAN 또는 USER_REPORTED_DELAY 중 기준일 이하 최신 CalculationResult 선택",
                    "case_id, basis_version, expected receipt, conflicts, highest priority",
                ],
                [
                    "상품 scenario match",
                    "역할·사업자유형·금융위험 코드로 hard exclusion, 후보와 미충족 요건 분리",
                    "matched_product_ids, rejected reasons, missing hard requirements",
                ],
                [
                    "로컬 RAG",
                    "각 상품 ID의 원문 PDF·primary page만 lexical 검색하고 PDF SHA와 index SHA 비교",
                    "상품별 page citation, excerpt, evidence_id",
                ],
                [
                    "브리핑 freeze",
                    "TradeCase·Shipment·PaymentObligation·risk·상품 evidence를 하나의 payload와 hash로 고정",
                    "동일 basis_version / payload hash",
                ],
                [
                    "고객용 PDF",
                    "이해하기 쉬운 위험·이유·대응 선택지, 내부 심사정보 비노출",
                    "Report audience=CUSTOMER, asset_path, content_hash",
                ],
                [
                    "RM용 PDF",
                    "계산 basis·근거 citation·확인 필요사항·상담 체크리스트 포함",
                    "Report audience=RM, same basis_version",
                ],
            ],
            [29 * mm, 88 * mm, 49 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("6.2 화면 대사와 선택", styles["h2"]))
    story.append(_dialogue("agent", "충돌 종합 보고서를 만들어 드릴까요?", styles))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["고객용·RM용 종합 보고서 생성", "나중에"], styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(
        _callout(
            "본 시연의 상품 프로필",
            "Demo 1·2는 borrower_type=CORPORATION과 적용하지 않을 상품 hard requirement를 사전 저장한다. 따라서 위험 분석과 무관한 사업자 유형 질문은 본 경로에 나타나지 않는다. 프로필이 없는 일반 사용자에게만 법인사업자/개인사업자 질문을 fallback으로 표시한다.",
            styles,
            background=PALE_TEAL,
            accent=KB_TEAL,
        )
    )
    story.append(Spacer(1, 2.5 * mm))
    story.append(
        _dialogue(
            "agent",
            "동일한 위험 snapshot으로 고객용·KB 직원용 브리핑을 생성하는 데 동의하십니까?",
            styles,
        )
    )
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["동의", "취소"], styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(
        _dialogue(
            "agent",
            "보고서가 생성되었습니다. 두 보고서는 동일한 계산 기준을 사용하며, 상품 안내에는 원문 PDF 페이지 근거가 포함됩니다.",
            styles,
        )
    )
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["고객용 PDF 받기", "RM용 PDF 받기"], styles))
    story.append(Spacer(1, 3 * mm))
    story.append(_dialogue("agent", "KB 무역금융 상담사에게 연결해 드릴까요?", styles))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_choice_buttons(["상담사 연결", "나중에"], styles))
    story.append(Spacer(1, 2.5 * mm))
    story.append(
        _callout(
            "상담사 연결 선택 결과",
            "상담사 연결 요청이 접수되었습니다.  ※ 데모에서는 연결 요청 화면까지만 제공합니다.",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )
    story.append(_p("6.3 기대 상품 표현", styles["h2"]))
    story.append(
        _table(
            [
                ["표시 항목", "화면 예", "원칙"],
                ["상품명", "KB수출팩토링", "catalog의 공식 상품명"],
                [
                    "검토 상태",
                    "B/L 수령 후 검토 가능",
                    "AVAILABLE_AFTER_BL 같은 내부 코드는 한글 라벨로 변환",
                ],
                [
                    "고려 이유",
                    "수출채권 조기 현금화 가능성을 검토",
                    "현재 위험 snapshot과 scenario match에서 생성",
                ],
                [
                    "근거",
                    "KB수출팩토링.pdf p.1",
                    "실제 로컬 PDF page citation 없으면 상품 옵션에서 제외",
                ],
                [
                    "주의",
                    "실제 이용 가능 여부는 KB 심사와 상품요건 확인 필요",
                    "추천·승인 확정 표현 금지",
                ],
            ],
            [29 * mm, 62 * mm, 75 * mm],
            styles,
        )
    )
    story.append(PageBreak())

    # DB evidence
    _add_section_title(
        story,
        "7",
        "DB 증거와 심사위원 설명법",
        styles,
        "DB 전체 테이블을 설명하는 대신, 각 데모가 남긴 최소 증거를 실행 전후로 비교한다. 원천 사실, 운영 projection, 계산 snapshot, 보고서가 분리되어 있다는 점이 핵심이다.",
    )
    story.append(_p("7.1 데모별 확인할 row", styles["h2"]))
    story.append(
        _table(
            [
                ["데모", "테이블", "화면에서 확인할 핵심 필드", "의미"],
                ["0", "IngestionBatch", "batch_id, status, file_count", "업로드 단위와 분석 상태"],
                [
                    "0",
                    "Document",
                    "doc_type, sha256, case_id, classification_status",
                    "원본 파일 분류·거래 배정",
                ],
                [
                    "0",
                    "DocumentFact",
                    "exact_standard_field, raw_value, normalized_json",
                    "문서에서 추출된 불변 원천 사실",
                ],
                [
                    "0",
                    "DocumentFieldOverride",
                    "field, raw_input, actor, previous_normalized_json",
                    "원천 Fact를 덮어쓰지 않는 Human 보완 이력",
                ],
                [
                    "0",
                    "TradeCase",
                    "company_id, transaction_id, status, basis_version",
                    "한 회사의 개별 무역 거래",
                ],
                [
                    "0",
                    "Shipment",
                    "booking_no, bl_no, etd, on_board_date, state_version",
                    "계획·실적 선적 상태 projection",
                ],
                [
                    "0/1/2",
                    "PaymentObligation",
                    "raw_text, anchor_type_effective, tenor_days, calculation_allowed",
                    "Invoice 지급조건의 결정론적 계산 계약",
                ],
                [
                    "1/2",
                    "FinancialEvent",
                    "event_id, event_type, event_date, amount, currency",
                    "회사 보유 금융 일정",
                ],
                [
                    "1/2",
                    "TransactionFinancialEventLink",
                    "transaction_id, event_id, link_status, dependency_scope",
                    "어느 거래가 어느 금융 일정에 얼마나 의존하는지",
                ],
                [
                    "1/2",
                    "CalculationResult",
                    "source_kind, as_of_date, basis_version, result_json",
                    "모니터링 또는 제보 지연의 계산 snapshot",
                ],
                [
                    "1/2",
                    "Conflict",
                    "gap_days, priority, reason, financial_event_id",
                    "충돌과 우선순위의 구조화 결과",
                ],
                [
                    "1",
                    "MonitoringRun / Alert",
                    "as_of_date, counts / signal_code, severity, dedup_key",
                    "수동 금일 분석 실행과 중복 방지 Alert",
                ],
                [
                    "0/2/보고서",
                    "Confirmation",
                    "thread_id, question_type, status, response_json",
                    "Human interrupt·resume 감사 증거",
                ],
                [
                    "1/2",
                    "Report",
                    "audience, basis_version, asset_path, content_hash",
                    "동일 basis의 고객용·RM용 PDF",
                ],
            ],
            [15 * mm, 39 * mm, 71 * mm, 41 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("7.2 원천 Fact와 운영 projection", styles["h2"]))
    story.append(
        _table(
            [
                ["구분", "예", "변경 원칙"],
                [
                    "원천 Fact",
                    "DocumentFact: Booking.ETD=2026-08-15, Invoice.PaymentTerms='T/T 45 DAYS AFTER ...'",
                    "원본에서 추출한 사실. Human 보완은 별도 DocumentFieldOverride로 append-only 기록",
                ],
                [
                    "운영 projection",
                    "Shipment.etd=2026-08-15, PaymentObligation.tenor_days=45",
                    "조회·계산을 위해 정규화한 현재 운영 상태. 근거 Fact와 basis를 추적 가능해야 함",
                ],
                [
                    "조건부 scenario",
                    "CalculationResult: 9일 지연 시 revised expected departure=2026-08-24",
                    "실제 B/L·on-board 사실을 변경하지 않음. is_scenario와 source_kind로 분리",
                ],
            ],
            [32 * mm, 72 * mm, 62 * mm],
            styles,
        )
    )
    story.append(
        _callout(
            "심사위원 설명 포인트",
            "LLM은 계획·위임·설명에 사용하고, 날짜·금액·우선순위 계산과 DB commit은 검증 가능한 Tool·Service가 수행합니다. "
            "그래서 같은 입력은 같은 계산 snapshot과 evidence ID로 재현할 수 있습니다.",
            styles,
            background=PALE_TEAL,
            accent=KB_TEAL,
        )
    )
    story.append(PageBreak())

    # Rehearsal
    _add_section_title(
        story,
        "8",
        "화면별 최종 리허설 체크리스트",
        styles,
        "각 화면에서 말할 문장, 오른쪽 Studio에서 보여줄 노드, 성공 판정을 미리 고정한다.",
    )
    story.append(_p("8.1 Demo 0 리허설", styles["h2"]))
    story.append(
        _table(
            [
                ["화면", "왼쪽 Streamlit", "오른쪽 Studio", "성공 판정"],
                [
                    "D0-01",
                    "새 대화, Thread ID 확인",
                    "같은 Thread 선택, Graph 탭",
                    "양쪽 thread_id 일치",
                ],
                [
                    "D0-02",
                    "새로운 무역파일 등록 클릭",
                    "Planning 활성",
                    "UPLOAD_TRADE_FILES 계획 생성",
                ],
                ["D0-03", "PDF 3개 업로드", "Document Intelligence 실행", "3종 문서 분류"],
                ["D0-04", "분석 중", "Trade Case Manager bundle", "거래 후보 1건"],
                [
                    "D0-05",
                    "누락 없음·거래 1건 반영 요약",
                    "commit → Critic PASS → Planning final",
                    "Human 질문 없이 DB evidence 확인",
                ],
                ["D0-06", "금융일정 등록 후속 버튼", "실행 없음", "Demo 0 종료"],
            ],
            [16 * mm, 55 * mm, 57 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("8.2 Demo 1 리허설", styles["h2"]))
    story.append(
        _table(
            [
                ["화면", "왼쪽 Streamlit", "오른쪽 Studio", "성공 판정"],
                [
                    "D1-01",
                    "금일 금융위험 거래 분석 클릭",
                    "Planning → Financial Calendar",
                    "후보 1건",
                ],
                [
                    "D1-02",
                    "분석 진행",
                    "Shipment Timeline → Financial Exposure",
                    "MONITORING_SCAN snapshot",
                ],
                ["D1-03", "P1·gap·이유 요약", "Critic → Report Writer", "Conflict·Alert 저장"],
                ["D1-04", "종합 보고서 생성 클릭", "Product Advisor", "위험 결과 자동 전달"],
                [
                    "D1-05",
                    "상품 후보·원문 근거 표시",
                    "사전 프로필 match → local RAG",
                    "불필요한 프로필 interrupt 없이 page citation 포함",
                ],
                [
                    "D1-06",
                    "보고서 생성 동의",
                    "build payload → customer → RM → Critic",
                    "same basis PASS",
                ],
                ["D1-07", "PDF 2종 다운로드", "실행 완료", "두 asset_path 유효"],
                ["D1-08", "상담사 연결 클릭", "추가 backend 실행 없음", "데모 전용 접수 문구"],
            ],
            [16 * mm, 55 * mm, 57 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("8.3 Demo 2 리허설", styles["h2"]))
    story.append(
        _table(
            [
                ["화면", "왼쪽 Streamlit", "오른쪽 Studio", "성공 판정"],
                [
                    "D2-01",
                    "CASE-DEMO2, 9일 지연 문장 입력",
                    "Planning → Shipment Timeline",
                    "ShipmentEvent + scenario",
                ],
                [
                    "D2-02",
                    "On-board date 적용",
                    "interrupt → same Thread resume",
                    "Payment Gate 통과",
                ],
                [
                    "D2-03",
                    "전액 선택",
                    "Financial Calendar link update → recalc",
                    "dependency_scope=FULL",
                ],
                [
                    "D2-04",
                    "계획/지연 회수일, 5일 gap, P1 요약",
                    "Financial Exposure → Critic",
                    "USER_REPORTED_DELAY snapshot",
                ],
                [
                    "D2-05",
                    "종합 보고서 생성",
                    "Product Advisor → local RAG",
                    "citation 있는 후보만 유지",
                ],
                [
                    "D2-06",
                    "동의 → PDF 2종",
                    "Report Writer → Critic",
                    "동일 basis·실제 Shipment 불변",
                ],
                ["D2-07", "상담사 연결 UI", "추가 backend 실행 없음", "접수 문구 표시"],
            ],
            [16 * mm, 55 * mm, 57 * mm, 38 * mm],
            styles,
            small=True,
        )
    )
    story.append(_p("8.4 시작 10분 전 최종 점검", styles["h2"]))
    story.append(
        _checklist(
            [
                "Demo 0·1·2 파일이 서로 다른 batch_id와 transaction_id를 사용한다.",
                "Demo 1 기준일이 2026-08-20으로 고정되어 있다.",
                "Demo 1 모니터링 후보는 1건만 활성화되어 있다.",
                "Demo 2 입력 문장을 클립보드에 준비했다.",
                "Human 버튼은 True/False, FULL/PARTIAL이 아니라 동의/취소, 전액/일부로 보인다.",
                "Payment Gate 선택지는 On-board date 적용/취소만 노출된다.",
                "Streamlit과 Studio가 같은 :2024 server와 동일 Thread를 사용한다.",
                "상품 OCR index와 source PDF SHA 검증이 통과한다.",
                "고객용·RM용 출력 PDF를 미리 열 수 있는지 확인했다.",
                "상담사 연결은 실제 전송이 아니라 데모용 접수 화면까지만임을 설명한다.",
                "API 잔액, 모델 환경변수, 네트워크 상태를 확인하되 리허설은 최소 출력으로 수행한다.",
            ],
            styles,
        )
    )
    story.append(_p("8.5 실패 방지 대체 진행", styles["h2"]))
    story.append(
        _table(
            [
                ["문제", "즉시 확인", "대체 진행"],
                [
                    "Studio에 실행이 안 보임",
                    "thread_id와 :2024 server 일치",
                    "Streamlit 상태 패널의 Thread ID를 복사해 Studio에서 재선택",
                ],
                [
                    "Demo 0 거래가 2건 이상으로 분리",
                    "세 문서 transaction_reference·party·항구 값",
                    "정확 매칭용 동일 reference가 있는 백업 3 PDF 사용",
                ],
                [
                    "Demo 1 후보 0건",
                    "monitoring_enabled와 FinancialEvent link",
                    "preflight 스크립트로 단일 후보 seed 복구",
                ],
                [
                    "Demo 1 날짜가 달라짐",
                    "as_of_date",
                    "UI 날짜를 2026-08-20으로 재설정 후 새 Thread",
                ],
                [
                    "Demo 2 거래를 못 찾음",
                    "CASE-DEMO2 case_id 포함 여부",
                    "준비된 정확 문장을 다시 붙여넣기",
                ],
                [
                    "상품 근거 0건",
                    "PDF SHA와 OCR index SHA",
                    "상품 후보를 확정하지 않고 '근거 인덱스 재검증 필요'로 정직하게 설명",
                ],
                [
                    "보고서 생성 중단",
                    "동의 응답과 동일 basis",
                    "동의 버튼으로 같은 Thread resume, 새 질문으로 재시작하지 않음",
                ],
            ],
            [42 * mm, 55 * mm, 69 * mm],
            styles,
            small=True,
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(
        _callout(
            "최종 데모 메시지",
            "KB TradeFlow Twin은 문서를 읽는 챗봇이 아니라, 무역 거래와 금융일정을 하나의 상태로 관리하고 선적 변화가 현금흐름에 미치는 영향을 먼저 계산한 뒤, "
            "근거가 있는 KB 상품과 고객·RM용 행동 자료까지 연결하는 멀티 Agent 무역 비서입니다.",
            styles,
            background=PALE_YELLOW,
            accent=KB_GOLD,
        )
    )

    return story


def generate(output_path: Path = OUTPUT_PATH) -> Path:
    _register_fonts()
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=19 * mm,
        bottomMargin=18 * mm,
        title="KB TradeFlow Twin Demo 0·1·2 시나리오 가이드",
        author="KB TradeFlow Twin",
        subject="공모전 최종 데모 사전자료, 화면 대사, Agent·Tool, Human resume, RAG, 보고서 및 DB 증거",
        creator="scripts/generate_final_demo_scenario_pdf.py",
    )
    document.build(
        _build_story(styles),
        onFirstPage=_draw_page,
        onLaterPages=_draw_page,
    )
    return output_path


def main() -> None:
    path = generate()
    print(path)


if __name__ == "__main__":
    main()
