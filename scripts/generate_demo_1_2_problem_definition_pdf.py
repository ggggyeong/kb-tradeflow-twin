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
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = (
    PROJECT_ROOT / "output" / "pdf" / "KB_TradeFlow_Twin_Demo_1_2_문제정의_및_PPT_제작가이드.pdf"
)

NAVY = colors.HexColor("#08243E")
BLUE = colors.HexColor("#1268B3")
GOLD = colors.HexColor("#F5B400")
TEAL = colors.HexColor("#159A95")
RED = colors.HexColor("#D94B4B")
INK = colors.HexColor("#172B43")
MUTED = colors.HexColor("#5D6C7B")
GRID = colors.HexColor("#CAD5E1")
PALE_BLUE = colors.HexColor("#EDF5FC")
PALE_GOLD = colors.HexColor("#FFF6D8")
PALE_RED = colors.HexColor("#FDEEEE")
PALE_TEAL = colors.HexColor("#EAF7F5")
LIGHT = colors.HexColor("#F5F7FA")


def _register_fonts() -> None:
    font_path = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"
    pdfmetrics.registerFont(TTFont("KBText", font_path))
    pdfmetrics.registerFont(TTFont("KBDisplay", font_path))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="KBDisplay",
            fontSize=26,
            leading=35,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=8 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="KBText",
            fontSize=12,
            leading=19,
            textColor=colors.white,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="KBDisplay",
            fontSize=20,
            leading=27,
            textColor=NAVY,
            spaceBefore=2 * mm,
            spaceAfter=5 * mm,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="KBDisplay",
            fontSize=14,
            leading=20,
            textColor=BLUE,
            spaceBefore=3 * mm,
            spaceAfter=3 * mm,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="KBText",
            fontSize=9.5,
            leading=15.5,
            textColor=INK,
            spaceAfter=2.5 * mm,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="KBText",
            fontSize=7.7,
            leading=11.5,
            textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["Normal"],
            fontName="KBDisplay",
            fontSize=8.2,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.white,
        ),
        "table_body": ParagraphStyle(
            "TableBody",
            parent=base["Normal"],
            fontName="KBText",
            fontSize=7.6,
            leading=11,
            textColor=INK,
        ),
        "callout_title": ParagraphStyle(
            "CalloutTitle",
            parent=base["Normal"],
            fontName="KBDisplay",
            fontSize=9.5,
            leading=13,
            textColor=NAVY,
        ),
        "callout_body": ParagraphStyle(
            "CalloutBody",
            parent=base["Normal"],
            fontName="KBText",
            fontSize=8.5,
            leading=13.5,
            textColor=INK,
        ),
        "cover_note_title": ParagraphStyle(
            "CoverNoteTitle",
            parent=base["Normal"],
            fontName="KBDisplay",
            fontSize=9.5,
            leading=13,
            textColor=GOLD,
        ),
        "cover_note_body": ParagraphStyle(
            "CoverNoteBody",
            parent=base["Normal"],
            fontName="KBText",
            fontSize=8.5,
            leading=13.5,
            textColor=colors.white,
        ),
        "center": ParagraphStyle(
            "Center",
            parent=base["Normal"],
            fontName="KBDisplay",
            fontSize=11,
            leading=16,
            alignment=TA_CENTER,
            textColor=INK,
        ),
    }


def _p(text: Any, style: ParagraphStyle, *, markup: bool = True) -> Paragraph:
    value = str(text)
    if not markup:
        value = html.escape(value)
    return Paragraph(value.replace("\n", "<br/>"), style)


def _table(
    rows: Sequence[Sequence[Any]],
    styles: dict[str, ParagraphStyle],
    widths: Sequence[float],
    *,
    header: bool = True,
) -> Table:
    prepared: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        row_style = styles["table_head"] if header and row_index == 0 else styles["table_body"]
        prepared.append(
            [
                cell if isinstance(cell, Paragraph) else _p(cell, row_style, markup=False)
                for cell in row
            ]
        )
    table = Table(prepared, colWidths=list(widths), repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ]
        )
    else:
        commands.append(("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, LIGHT]))
    table.setStyle(TableStyle(commands))
    return table


def _callout(
    title: str,
    text: str,
    styles: dict[str, ParagraphStyle],
    *,
    background: colors.Color = PALE_BLUE,
    accent: colors.Color = BLUE,
) -> Table:
    content = Table(
        [[_p(title, styles["callout_title"]), _p(text, styles["callout_body"])]],
        colWidths=[38 * mm, 124 * mm],
    )
    content.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("LINEBEFORE", (0, 0), (0, 0), 4, accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return content


def _flow(
    items: Iterable[str],
    styles: dict[str, ParagraphStyle],
    *,
    accent: colors.Color = BLUE,
) -> Table:
    values = list(items)
    row: list[Any] = []
    widths: list[float] = []
    item_width = 31 * mm
    arrow_width = 6 * mm
    for index, item in enumerate(values):
        row.append(_p(item, styles["small"], markup=False))
        widths.append(item_width)
        if index < len(values) - 1:
            row.append(_p("→", styles["center"], markup=False))
            widths.append(arrow_width)
    table = Table([row], colWidths=widths, hAlign="LEFT")
    commands: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    for index in range(0, len(row), 2):
        commands.extend(
            [
                ("BACKGROUND", (index, 0), (index, 0), PALE_BLUE),
                ("BOX", (index, 0), (index, 0), 0.7, accent),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def _cover(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, 0, width, 7 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(3)
    canvas.line(width - 48 * mm, height - 42 * mm, width - 18 * mm, height - 42 * mm)
    canvas.line(width - 33 * mm, height - 57 * mm, width - 33 * mm, height - 27 * mm)
    canvas.restoreState()


def _header_footer(canvas: Any, doc: Any) -> None:
    if doc.page == 1:
        _cover(canvas, doc)
        return
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, height - 13 * mm, width - 18 * mm, height - 13 * mm)
    canvas.setFont("KBText", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, height - 10 * mm, "KB TradeFlow Twin")
    canvas.drawRightString(
        width - 18 * mm, height - 10 * mm, "Demo 1·2 문제정의 및 PPT 제작 가이드"
    )
    canvas.drawString(18 * mm, 10 * mm, "심사위원 사전 설명용 합성 데모 자료")
    canvas.drawRightString(width - 18 * mm, 10 * mm, str(doc.page))
    canvas.restoreState()


def build_pdf() -> Path:
    _register_fonts()
    styles = _styles()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=17 * mm,
        title="KB TradeFlow Twin Demo 1·2 문제정의 및 PPT 제작 가이드",
        author="KB TradeFlow Twin",
    )

    story: list[Any] = []

    story.append(Spacer(1, 45 * mm))
    story.append(_p("KB TradeFlow Twin", styles["title"]))
    story.append(_p("Demo 1·2 문제정의 및 PPT 제작 가이드", styles["title"]))
    story.append(
        _p(
            "무역팀이 심사위원에게 두 데모의 상황을 먼저 설명하고, 이후 멀티에이전트 시연으로 자연스럽게 연결하기 위한 전달 문서",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 28 * mm))
    cover_note = Table(
        [
            [
                _p("문서의 목적", styles["cover_note_title"]),
                _p(
                    "Booking·Invoice·B/L의 관계를 모르는 심사위원도 두 기업의 문제와 금융 충돌을 이해하도록 설명한다. 실제 PPT에서는 코드보다 기업 상황, 날짜 변화, 자금 공백, Agent 협업 결과를 중심으로 보여준다.",
                    styles["cover_note_body"],
                ),
            ]
        ],
        colWidths=[38 * mm, 124 * mm],
    )
    cover_note.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#153854")),
                ("LINEBEFORE", (0, 0), (0, 0), 4, GOLD),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(cover_note)
    story.append(PageBreak())

    story.append(_p("1. 자료 사용 전 반드시 설명할 경계", styles["h1"]))
    story.append(
        _callout(
            "합성 데모 데이터",
            "원본 데모1_2_간소화_시나리오.xlsx의 문제 상황과 업무 맥락을 참고했지만, 날짜·금액·회사·거래 ID는 현재 DB 구조와 위험 계산이 정확히 성립하도록 새로 구성했다. 실제 기업·선사·금융거래가 아니다.",
            styles,
            background=PALE_GOLD,
            accent=GOLD,
        )
    )
    story.append(_p("1.1 두 데모의 공통 출발점", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "공통 상황", "심사위원에게 설명할 의미"],
                [
                    "보유 문서",
                    "Booking + Commercial Invoice",
                    "예정 선적과 판매·결제조건은 확인 가능",
                ],
                ["미보유 문서", "실제 Bill of Lading", "실제 본선적재일과 선적 증거는 아직 미확정"],
                [
                    "Booking의 B/L No.",
                    "예약·예정 참조번호",
                    "번호가 있어도 실제 B/L 발급을 의미하지 않음",
                ],
                [
                    "계산 원칙",
                    "PaymentTerms를 날짜 규칙으로 해석",
                    "LLM이 날짜를 추측하지 않고 Tool이 계산",
                ],
                [
                    "최종 대응",
                    "위험 요약 → 상품 근거 → 고객/RM 보고서",
                    "발견에서 대응까지 하나의 대화로 연결",
                ],
            ],
            styles,
            [33 * mm, 55 * mm, 76 * mm],
        )
    )
    story.append(_p("1.2 무역 흐름", styles["h2"]))
    story.append(
        _flow(
            [
                "Booking\n예정 선적",
                "실제 선적",
                "B/L\n본선적재일",
                "Invoice\n결제조건",
                "금융일정\n충돌 판단",
            ],
            styles,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        _callout(
            "핵심 문제 정의",
            "결제조건이 B/L 본선적재일 + N일이면 선적 지연이 곧 수출대금 회수 지연이다. 그러나 기업은 그 변화가 대출 만기나 공급업체 지급일과 언제 충돌하는지 즉시 계산하기 어렵다.",
            styles,
            background=PALE_RED,
            accent=RED,
        )
    )
    story.append(PageBreak())

    story.append(_p("2. Demo 1 - 사전 통보 없이 B/L을 기다리는 상황", styles["h1"]))
    story.append(
        _callout(
            "문제 정의 ② + ③",
            "선적 지연을 미리 통보받지 못한 기업이 B/L을 기다리는 동안, 대출 만기 전에 수출대금을 회수할 수 있는 마지막 안전 시점을 지나칠 수 있다. 위험을 발견해도 어떤 금융상품을 검토해야 하는지 찾기 어렵다.",
            styles,
            background=PALE_RED,
            accent=RED,
        )
    )
    story.append(_p("2.1 기업과 보유 문서", styles["h2"]))
    story.append(
        _table(
            [
                ["구분", "실제 데모 값", "의미"],
                ["기업", "HAEORUM TEXTILE CO., LTD. / DEMO1-CO", "수출기업"],
                ["거래", "CASE-DEMO1 / TXN-DEMO1", "문서·금융일정을 연결하는 거래"],
                ["Booking", "BK-DEMO1-0001 / REF-DEMO1", "예정 선적정보"],
                ["예정 선적일", "2026-08-05", "위험 계산의 계획 proxy"],
                ["선박·항로", "KB SUNRISE 210S / 부산 → 싱가포르", "Shipment 계획"],
                ["화물", "NYLON OXFORD FABRIC", "Invoice와 Booking 매칭 근거"],
                ["Invoice", "INV-DEMO1-0001 / USD 25,000", "판매대금과 결제조건"],
                ["결제조건", "T/T 60일, 본선적재일 기준", "실제 선적일 + 60일에 회수"],
                ["B/L", "실제 문서 없음", "본선적재일을 아직 검증할 수 없음"],
            ],
            styles,
            [32 * mm, 63 * mm, 69 * mm],
        )
    )
    story.append(_p("2.2 문서가 같은 거래로 묶이는 근거", styles["h2"]))
    story.append(
        _table(
            [
                ["매칭 관계", "일치 값", "점수"],
                ["Invoice seller = Booking shipper", "HAEORUM TEXTILE CO., LTD.", "50"],
                ["Invoice goods = Booking commodity", "NYLON OXFORD FABRIC", "40"],
                ["총점", "동일 거래 판정", "90 / MATCH"],
            ],
            styles,
            [65 * mm, 70 * mm, 29 * mm],
        )
    )
    story.append(PageBreak())

    story.append(_p("3. Demo 1 - 금융상황과 선제 위험 계산", styles["h1"]))
    story.append(_p("3.1 회사 금융일정", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "값", "거래와의 관계"],
                ["금융 이벤트", "운전자금대출 만기", "수출대금을 대출 상환재원으로 사용"],
                ["Event ID", "EVT-DEMO1-LOAN", "TXN-DEMO1과 CONFIRMED 연결"],
                ["만기일", "2026-10-10", "이 날짜 전 수출대금 회수가 필요"],
                ["금액", "KRW 30,000,000", "상환 대상 금액"],
                ["금융기관", "KB국민은행", "금융 이벤트 보유 기관"],
                ["분석 기준일", "2026-08-20", "금일 분석 버튼의 가상 기준일"],
            ],
            styles,
            [37 * mm, 54 * mm, 73 * mm],
        )
    )
    story.append(_p("3.2 날짜 계산", styles["h2"]))
    story.append(
        _flow(
            [
                "대출 만기\n10월 10일",
                "결제조건\n+60일",
                "안전 선적 한계\n8월 11일",
                "금일\n8월 20일",
                "9일 초과\n선제 경고",
            ],
            styles,
            accent=RED,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        _table(
            [
                ["계산", "값", "해석"],
                [
                    "계획 회수일",
                    "2026-08-05 + 60일 = 2026-10-04",
                    "계획대로면 대출 만기 6일 전 회수",
                ],
                [
                    "최종 안전 본선적재일",
                    "2026-10-10 - 60일 = 2026-08-11",
                    "이날보다 늦으면 만기 전 회수 곤란",
                ],
                [
                    "금일 기준 초과",
                    "2026-08-20 - 2026-08-11 = 9일",
                    "B/L 미확정 상태에서 안전 한계 경과",
                ],
                [
                    "위험 결과",
                    "PREEMPTIVE_BREACH / ACTION_REQUIRED",
                    "미선적 단정이 아닌 확인·대응 필요 경고",
                ],
            ],
            styles,
            [43 * mm, 58 * mm, 63 * mm],
        )
    )
    story.append(
        _callout(
            "발표할 때 주의",
            "B/L이 없다는 사실만으로 선박이 출항하지 않았다고 말하지 않는다. 확인 가능한 B/L·본선적재일이 없는 상태에서 금융 안전 한계를 넘었다고 표현한다.",
            styles,
            background=PALE_GOLD,
            accent=GOLD,
        )
    )
    story.append(PageBreak())

    story.append(_p("4. Demo 1 - 시연 스토리보드", styles["h1"]))
    story.append(
        _table(
            [
                ["순서", "사용자 화면", "Agent 협업", "심사 포인트"],
                [
                    "1",
                    "금일 금융위험 거래 분석 선택",
                    "Planning → Financial Calendar",
                    "후보 거래 자동 선정",
                ],
                ["2", "분석 중", "Shipment Timeline → Financial Exposure", "문서와 금융일정 결합"],
                ["3", "안전 한계 9일 초과 요약", "날짜 Tool → Critic", "선제 위험 탐지"],
                ["4", "종합 보고서 생성 선택", "Product Advisor → local RAG", "KB 상품 원문 근거"],
                ["5", "보고서 생성 동의", "Report Writer → Critic", "고객용·RM용 동일 근거"],
                [
                    "6",
                    "PDF 2종과 상담사 연결 버튼",
                    "추가 외부 연결 없음",
                    "발견에서 대응까지 완결",
                ],
            ],
            styles,
            [15 * mm, 46 * mm, 55 * mm, 48 * mm],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(
        _callout(
            "PPT 한 줄 메시지",
            "Demo 1은 기업이 아직 인지하지 못한 B/L 미수령 위험을 오늘 날짜 기준으로 먼저 찾아주는 시연이다.",
            styles,
            background=PALE_TEAL,
            accent=TEAL,
        )
    )
    story.append(_p("4.1 심사위원에게 보여줄 결과", styles["h2"]))
    story.append(
        _table(
            [
                ["결과", "표시 내용"],
                ["위험 요약", "대출 만기 전 회수 가능 시점 초과와 발생 이유"],
                ["우선순위", "즉시 확인과 대응이 필요한 거래"],
                ["상품 근거", "해시가 검증된 KB 상품 PDF의 페이지 인용"],
                ["고객용 보고서", "현재 위험·확인사항·다음 행동 중심"],
                ["RM용 보고서", "동일 계산 basis + 상담 체크리스트·근거 ID"],
                ["상담사 연결", "데모에서는 연결 요청 접수 화면까지만 표시"],
            ],
            styles,
            [45 * mm, 119 * mm],
        )
    )
    story.append(PageBreak())

    story.append(_p("5. Demo 2 - 9일 선적 지연을 통보받은 상황", styles["h1"]))
    story.append(
        _callout(
            "문제 정의 ① + ③",
            "선사로부터 지연 사실을 들었지만, 그 변화가 수출대금 회수일과 공급업체 지급일에 어떤 영향을 주는지 즉시 알기 어렵다. 위험을 계산한 뒤에도 현재 기업에 맞는 대응상품을 찾기 어렵다.",
            styles,
            background=PALE_RED,
            accent=RED,
        )
    )
    story.append(_p("5.1 기업과 보유 문서", styles["h2"]))
    story.append(
        _table(
            [
                ["구분", "실제 데모 값", "의미"],
                ["기업", "HANBIT PRECISION CO., LTD. / DEMO2-CO", "수출기업"],
                ["거래", "CASE-DEMO2 / TXN-DEMO2", "문서·금융일정을 연결하는 거래"],
                ["Booking", "BK-DEMO2-0001 / REF-DEMO2", "예정 선적정보"],
                ["예정 선적일", "2026-08-15", "지연 전 계획 기준"],
                ["선박·항로", "KB SAKURA 303E / 부산 → 요코하마", "Shipment 계획"],
                ["화물", "AUTOMOTIVE CONTROL MODULES", "Invoice와 Booking 매칭 근거"],
                ["Invoice", "INV-DEMO2-0001 / USD 60,000", "판매대금과 결제조건"],
                ["결제조건", "T/T 45일, 본선적재일 기준", "실제 선적일 + 45일에 회수"],
                ["B/L", "실제 문서 없음", "지연 시나리오로만 영향 분석"],
            ],
            styles,
            [32 * mm, 67 * mm, 65 * mm],
        )
    )
    story.append(_p("5.2 사용자가 입력하는 문장", styles["h2"]))
    story.append(
        _callout(
            "사용자 제보",
            "CASE-DEMO2 거래의 선적이 9일 늦어질 예정입니다. 영향을 분석해 주세요.",
            styles,
            background=PALE_BLUE,
            accent=BLUE,
        )
    )
    story.append(PageBreak())

    story.append(_p("6. Demo 2 - 금융상황과 예상 충돌", styles["h1"]))
    story.append(_p("6.1 회사 금융일정", styles["h2"]))
    story.append(
        _table(
            [
                ["항목", "값", "거래와의 관계"],
                ["금융 이벤트", "공급업체 지급", "수출대금을 지급 재원으로 사용"],
                ["Event ID", "EVT-DEMO2-SUPPLIER", "TXN-DEMO2와 CONFIRMED 연결"],
                ["지급일", "2026-10-03", "이날 공급업체에 자금 지급"],
                ["지급금액", "KRW 45,000,000", "거래 대금 의존범위를 Human 확인"],
                ["금융기관", "KB국민은행", "금융 이벤트 보유 기관"],
                ["제보일", "2026-08-20", "사용자가 지연 사실을 입력한 날짜"],
            ],
            styles,
            [37 * mm, 54 * mm, 73 * mm],
        )
    )
    story.append(_p("6.2 9일 지연 전후 계산", styles["h2"]))
    story.append(
        _table(
            [
                ["단계", "지연 전", "9일 지연 후", "영향"],
                ["예상 본선적재일", "2026-08-15", "2026-08-24", "+9일"],
                ["결제조건", "+45일", "+45일", "동일 규칙"],
                ["예상 수출대금 회수일", "2026-09-29", "2026-10-08", "+9일"],
                ["공급업체 지급일", "2026-10-03", "2026-10-03", "고정"],
                ["결과", "회수 후 지급 가능", "지급이 회수보다 5일 먼저", "5일 자금 공백"],
            ],
            styles,
            [43 * mm, 38 * mm, 38 * mm, 45 * mm],
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        _flow(
            [
                "계획 회수\n9월 29일",
                "9일 지연",
                "변경 회수\n10월 8일",
                "공급업체 지급\n10월 3일",
                "5일 부족\nP1",
            ],
            styles,
            accent=RED,
        )
    )
    story.append(PageBreak())

    story.append(_p("7. Demo 2 - Human 확인과 시연 스토리보드", styles["h1"]))
    story.append(_p("7.1 임의 계산을 막는 Human 확인", styles["h2"]))
    story.append(
        _table(
            [
                ["질문", "선택지", "데모 답변", "DB·계산 영향"],
                [
                    "B/L DATE를 On-board date로 적용할까요?",
                    "적용 / 취소",
                    "적용",
                    "Payment Gate 통과",
                ],
                [
                    "공급업체 지급이 수출대금 전액 또는 일부에 의존합니까?",
                    "전액 / 일부 / 취소",
                    "전액",
                    "dependency_scope=FULL 후 재계산",
                ],
            ],
            styles,
            [58 * mm, 36 * mm, 25 * mm, 45 * mm],
        )
    )
    story.append(
        _callout(
            "일부를 선택하면",
            "연결 금액과 통화를 추가 질문한다. 데모에서는 전액을 선택해 수출대금 지연이 공급업체 지급 재원 전체에 영향을 주는 상황을 보여준다.",
            styles,
            background=PALE_GOLD,
            accent=GOLD,
        )
    )
    story.append(_p("7.2 시연 순서", styles["h2"]))
    story.append(
        _table(
            [
                ["순서", "사용자 화면", "Agent 협업", "심사 포인트"],
                ["1", "9일 지연 문장 입력", "Planning → Shipment Timeline", "조건부 지연 시나리오"],
                ["2", "On-board date 적용", "Human interrupt → 같은 Thread resume", "기준일 검증"],
                ["3", "전액 선택", "Financial Calendar link 보완", "의존범위 명시"],
                ["4", "5일 자금 공백·P1 요약", "Financial Exposure → Critic", "결정론 계산"],
                ["5", "종합 보고서 생성", "Product Advisor → local RAG", "상품 원문 근거"],
                ["6", "PDF 2종·상담사 연결", "Report Writer → Critic", "동일 basis 검증"],
            ],
            styles,
            [15 * mm, 48 * mm, 54 * mm, 47 * mm],
        )
    )
    story.append(
        _callout(
            "PPT 한 줄 메시지",
            "Demo 2는 기업이 전달받은 선적 지연 정보를 입력하면, 물류 일정의 변화를 금융일정 충돌로 즉시 환산하는 시연이다.",
            styles,
            background=PALE_TEAL,
            accent=TEAL,
        )
    )
    story.append(PageBreak())

    story.append(_p("8. 두 데모 비교와 PPT 권장 구성", styles["h1"]))
    story.append(_p("8.1 상황 비교", styles["h2"]))
    story.append(
        _table(
            [
                ["구분", "Demo 1", "Demo 2"],
                ["문제", "지연 사실을 미리 알지 못함", "9일 지연을 미리 통보받음"],
                ["시작", "금일 금융위험 거래 분석", "사용자가 지연 사실 입력"],
                ["보유 문서", "Booking + Invoice", "Booking + Invoice"],
                ["미보유", "실제 B/L", "실제 B/L"],
                ["결제조건", "본선적재일 + 60일", "본선적재일 + 45일"],
                ["금융 이벤트", "운전자금대출 만기", "공급업체 지급"],
                ["핵심 위험", "안전 선적 한계 9일 초과", "수출대금 회수 5일 부족"],
                ["Agent 가치", "위험을 먼저 발견", "제보 영향을 즉시 재계산"],
                ["공통 결과", "상품 근거 + 고객/RM 보고서", "상품 근거 + 고객/RM 보고서"],
            ],
            styles,
            [37 * mm, 63 * mm, 64 * mm],
        )
    )
    story.append(_p("8.2 PPT 앞부분 권장 목차", styles["h2"]))
    story.append(
        _table(
            [
                ["슬라이드", "제목", "반드시 담을 내용"],
                ["1", "무역 문서와 금융일정의 연결", "Booking → B/L → 결제조건 → 금융일정 흐름"],
                [
                    "2",
                    "현재 기업이 겪는 문제",
                    "사전 통보가 없거나 있어도 금융영향을 즉시 알기 어려움",
                ],
                ["3", "Demo 1 상황", "문서·대출만기·8월 11일 안전 한계·9일 초과"],
                ["4", "Demo 2 상황", "9일 지연·회수일 10월 8일·공급업체 지급 10월 3일·5일 공백"],
                ["5", "왜 멀티에이전트인가", "문서·선적·금융·상품·보고서를 전문 Agent가 독립 수행"],
                ["6", "시연 전환", "이제 같은 상황을 Chat과 LangGraph Studio에서 실행"],
            ],
            styles,
            [19 * mm, 54 * mm, 91 * mm],
        )
    )
    story.append(_p("8.3 최종 발표 문장", styles["h2"]))
    story.append(
        _callout(
            "핵심 메시지",
            "KB TradeFlow Twin은 문서를 읽는 챗봇이 아니라, 개별 TradeCase의 물류 일정과 기업 금융일정을 연결해 위험을 먼저 발견하거나 즉시 재계산하고, 근거 있는 KB 상품과 고객·RM 보고서까지 제공하는 무역금융 멀티에이전트다.",
            styles,
            background=PALE_GOLD,
            accent=GOLD,
        )
    )

    document.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build_pdf())
