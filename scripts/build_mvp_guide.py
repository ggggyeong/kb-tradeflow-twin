"""Historical fixed-excerpt MVP guide builder, NOT the current RAG design.

The retained docs/TradeFlow-MVP-Guide.pdf describes manifest v3: preselected
answer spans and LLM quote selection. Read docs/service-mvp.md and
docs/rag-corpus-guide.md for the current multi-passage retrieval and grounded
explanation design. Legacy regeneration requires an explicit flag and v3 data;
its outputs must not overwrite the current verification summary or guide.
"""

from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from app.services.report_generator import (  # noqa: E402
    BLUE,
    _footer,
    _register_font,
    _safe,
    _styles,
    _table,
)


def main() -> None:
    if "--legacy-fixed-excerpt" not in sys.argv:
        raise SystemExit(
            "이 스크립트는 고정 본문 발췌 방식의 이전 v3 설명서 생성기입니다. "
            "현재 구조는 docs/service-mvp.md와 docs/rag-corpus-guide.md를 확인하세요. "
            "과거 실행 자료로만 재현하려면 --legacy-fixed-excerpt를 명시하세요."
        )
    result = json.loads((ROOT / "output/example/live-result.json").read_text())
    verification = result["verification"]
    manifest = json.loads(
        (ROOT / "data/knowledge/chroma_products/product_vector_manifest.json").read_text()
    )
    if manifest.get("schema_version") != "product-vector-manifest-v3" or any(
        option.get("explanation_points") for option in result.get("product_options", [])
    ):
        raise SystemExit(
            "현재 검색·설명 생성 결과를 이전 발췌 방식 설명서에 사용할 수 없습니다. "
            "과거 v3 색인과 같은 실행의 결과가 필요합니다. 파일은 변경하지 않았습니다."
        )
    summary = {
        "scope": "One synthetic export transaction; not a financial accuracy benchmark",
        "run_id": verification["run_id"],
        "model": verification["model"],
        "served_service_tiers": verification["served_service_tiers"],
        "llm_calls": result["llm_calls"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "status": result["status"],
        "checks": verification["checks"],
        "indexed_records": manifest["record_count"],
        "source_count": manifest["product_count"],
        "services": [
            {
                "event_id": c["event_id"],
                "service_code": c["service_code"],
                "status": c["status"],
                "source_ids": [o["product_id"] for o in c["information"]],
            }
            for c in result["service_cards"]
        ],
        "warnings": result["warnings"],
    }
    (ROOT / "docs/verification-summary-legacy-v3.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    font = _register_font()
    styles = _styles(font)
    styles["body"].fontSize, styles["body"].leading = 10, 16
    styles["cell"].fontSize, styles["cell"].leading = 8.8, 13.5
    styles["small"].fontSize, styles["small"].leading = 8.2, 12.5
    story: list[Any] = []

    def p(text: str, kind: str = "body") -> None:
        story.append(Paragraph(_safe(text), styles[kind]))
        story.append(Spacer(1, 2.5 * mm))

    def title(number: int, heading: str, subtitle: str) -> None:
        if number > 1:
            story.append(PageBreak())
        p(f"TRADEFLOW / LEGACY FIXED-EXCERPT V3 / 0{number}", "small")
        p(heading, "title")
        p(subtitle, "subtitle")
        story.append(HRFlowable(width="100%", thickness=1.2, color=BLUE, spaceAfter=5 * mm))

    def table(rows: list[list[str]], widths: list[int]) -> None:
        story.append(_table(rows, styles=styles, widths=[v * mm for v in widths]))
        story.append(Spacer(1, 4 * mm))

    title(
        1,
        "금융일정 충돌을\n상담 준비 정보로",
        "이전 v3 기록 · 고정 본문 발췌 방식 · 현재 RAG 설계와 다릅니다",
    )
    p("무엇을 해결하나요?", "heading")
    p(
        "수출기업 담당자가 여러 무역서류의 날짜·금액을 모으고, 입금일보다 먼저 돌아오는 금융일정을 확인한 뒤 관련 금융자료를 찾는 반복 업무를 연결했습니다."
    )
    p("서비스의 끝은 담당자 검토용 보고서입니다. 실제 대출 승인·계약 변경·송금은 하지 않습니다.")
    table(
        [
            ["역할", "하는 일"],
            [
                "Supervisor",
                "요청에 필요한 작업을 계획하고 허용된 순서로 도구를 호출합니다. 별도 Planner는 두지 않습니다.",
            ],
            [
                "Document Agent",
                "PDF의 텍스트 또는 OCR 결과에서 필드를 찾고 원문·페이지와 대조합니다.",
            ],
            [
                "Finance Advisor Agent",
                "엑셀 거래 연결과 날짜 비교 도구를 호출하고 서비스에 맞는 RAG 본문의 핵심 문장을 선택합니다.",
            ],
            [
                "Report Generator",
                "앞 단계의 결과를 PDF에 배치하는 Python 도구입니다. 새로운 판단을 만들지 않습니다.",
            ],
        ],
        [45, 129],
    )
    p("한 번의 분석 흐름", "heading")
    for line in [
        "요청 + 거래 ID + 무역서류 PDF + 금융일정 Excel",
        "→ Supervisor 계획 → Document Agent 추출·검증",
        "→ Finance Agent: 일정 비교 → 서비스 결정 → RAG",
        "→ 정보·근거·추가 질문 → PDF 보고서",
    ]:
        p(line)
    p("AI는 해석과 원문 선택, 코드는 계산과 통제, 사람은 최종 확인을 맡습니다.", "small")

    title(
        2,
        "세 충돌, 세 서비스",
        "분류를 늘리기보다 각 상황에서 고객에게 무엇을 줄지 명확히 했습니다.",
    )
    cases = [
        (
            "01 대출 만기",
            "입금 전에 대출 상환일이 도래합니다.",
            "대출 상환 방법·미상환 시 유의사항·연장 상담 관련 정보를 제공합니다.",
            "잔액과 다른 입금으로 상환할 수 있는지, 기존 은행에 상담했는지 확인합니다.",
            "새 대출로 기존 대출을 갚을 수 있다고 추천하지 않습니다.",
        ),
        (
            "02 선물환 매도 결제",
            "같은 통화의 외화 입금보다 기존 매도 계약 결제일이 빠릅니다.",
            "약정된 결제 의무·환율 손실·중도해지 시 확인 사항을 제공합니다.",
            "계약 통화·금액과 확보 외화, 은행의 일정 변경 가능 여부·정산 조건을 확인합니다.",
            "신규 파생상품이나 자동 만기 연장을 제안하지 않습니다.",
        ),
        (
            "03 공급자 지급",
            "입금 전에 공급자에게 지급할 일정이 있습니다.",
            "운전자금 용도·조건을 제공합니다. 수출채권이 확인된 경우에만 매입 선결 조건·환매 의무·비용 자료를 추가합니다.",
            "실제 필요한 금액과 지급 목적·증빙을 확인합니다.",
            "수출채권 매입을 무조건 가능한 자금조달이나 비소구 상품으로 설명하지 않습니다.",
        ),
    ]
    for heading, situation, service, question, limit in cases:
        p(heading, "heading")
        p("상황: " + situation)
        p("제공 정보: " + service)
        p("추가 질문: " + question, "small")
        p("범위 제한: " + limit, "small")
    p(
        "보고서는 자료에서 확인한 내용과 서비스가 정한 추가 질문을 구분합니다. 날짜 차이는 실제 자금 부족액이 아닙니다.",
        "small",
    )

    title(
        3,
        "ChromaDB에 무엇을 넣었나",
        "공식 PDF 4개 / 전체 48페이지 중 11페이지 / 검토한 본문 12구간",
    )
    table(
        [
            ["자료", "PDF 페이지", "남긴 본문 주제"],
            ["신한은행 기업대출 설명서", "12·17·18·19", "운전자금 용도·거래 조건 / 상환·연체·연장"],
            ["HSBC 수출입 및 보증거래 설명서", "1", "수출환어음 매입 비용"],
            ["HSBC 수출채권매입 약정서", "4·5·7", "매입 선결 조건 / 환매 의무 / 제출 서류"],
            ["중국은행 선물환 고객매도 설명서", "3·4·5", "매도 계약 / 환율 손실 / 중도해지"],
        ],
        [63, 29, 82],
    )
    p("왜 본문 구간을 따로 골랐나요?", "heading")
    p(
        "한 PDF에는 여러 상품과 금리 예시·서식·목차가 섞여 있습니다. 페이지를 통째로 넣는 대신, 카탈로그에 주제와 시작·끝 문구를 기록해 검토한 구간만 잘랐습니다. 경계 문구가 사라지거나 중복되면 색인 생성을 중단합니다."
    )
    p("저장하는 내용", "heading")
    p(
        "원문 조각 + 로컬 E5 임베딩(384차원) + 자료 ID + 충돌 코드 + 본문 주제 + 구간 ID + 파일명·페이지·SHA-256·공식 URL을 저장합니다."
    )
    p(
        f"현재 Chroma 레코드는 {manifest['record_count']}개입니다. 같은 본문을 두 충돌 유형에 연결한 레코드가 있어 고유 본문 12개와 수가 다릅니다. 고객 엑셀·무역서류는 공용 상품 DB에 넣지 않습니다."
    )
    p("원문 확보 경로", "heading")
    for text, url in [
        (
            "신한은행: 카탈로그의 원문 PDF 링크",
            "https://img.shinhan.com/sbank2016/form/20110131618000010051WF00001000000001.PDF",
        ),
        ("HSBC: 공식 기업금융 약관 목록", "https://www.hsbc.co.kr/ko-kr/cmb-agreement"),
        (
            "중국은행: 공식 상품설명서 게시글",
            "https://www.bankofchina.com/kr/kr/bocinfo/bi4/202205/t20220505_21084724.html",
        ),
    ]:
        story.append(
            Paragraph(f'<link href="{url}" color="#2F6FED">{_safe(text)}</link>', styles["small"])
        )
    p(
        "원본 PDF와 발췌 보고서는 로컬 검토용입니다. 공개 저장소에는 링크·카탈로그·코드와 이 설명서만 제공합니다.",
        "small",
    )

    title(4, "분류에서 근거까지", "RAG 검색 경로와 중단 조건을 코드로 고정했습니다.")
    table(
        [
            ["단계", "실제 처리"],
            [
                "1. 입력 검증",
                "엑셀에서 거래 ID·문서번호를 연결합니다. 지원 결제조건과 기준일이 확인될 때 예상 입금일을 계산합니다.",
            ],
            [
                "2. 규칙 분류",
                "입금이 지급보다 늦으면 충돌, 같은 날이면 확인 필요. 연결·날짜 미확인도 판단을 보류합니다.",
            ],
            [
                "3. 서비스 선택",
                "service_policy.py의 세 정책이 서비스·검색 주제·추가 질문을 결정합니다.",
            ],
            [
                "4. 후보 제한",
                "검토 완료 자료에서 충돌 코드·거래 방향·지급 목적·수출채권 확인 조건을 적용합니다.",
            ],
            [
                "5. 주제별 검색",
                "Chroma where에 충돌 코드 + 자료 ID + 주제를 함께 적용합니다. 주제마다 본문 1개를 검색합니다.",
            ],
            [
                "6. 근거 설명",
                "LLM이 각 자료의 핵심 원문을 선택해 제출합니다. 자유 금융 요약은 생성하지 않습니다. 출처 조합과 원문 문장의 실제 일치를 검증합니다.",
            ],
            [
                "7. 보고",
                "상황·관련 정보·파일/페이지·확인 질문·보류 사유를 서비스 카드로 묶어 PDF에 배치합니다.",
            ],
        ],
        [32, 142],
    )
    p("검색을 멈추는 경우", "heading")
    p(
        "충돌 없음 → 검색 생략. 입력 미확인 → 확인 요청. 관련 자료 없음·색인 변경·잘못된 출처 → 해당 설명 보류. 다른 상품으로 자동 대체하지 않습니다."
    )
    p("프롬프트가 맡는 역할", "heading")
    p(
        "Supervisor는 요청 범위와 순서, Document는 필드 후보와 원문 복사, Finance는 서비스 범위와 핵심 원문 선택을 지시합니다. 날짜 계산·검색 필터·호출 횟수는 프롬프트만 믿지 않고 코드가 통제합니다."
    )
    p(
        "실제 nano 실행의 금융 요약이 다른 자료의 조건을 섞어 발췌 중심으로 바꿨습니다. 원문 일치도 문장 선택의 관련성·완전성까지 증명하지는 않습니다.",
        "small",
    )

    title(
        5,
        "어디를 읽고, 무엇을 검증했나",
        "면접에서는 서비스 흐름 → 책임 분리 → 근거 검증 순서로 설명하면 됩니다.",
    )
    table(
        [
            ["파일", "설명할 핵심"],
            [
                "agents/supervisor.py\nservices/portfolio_pipeline.py",
                "계획·허용 도구·단계 순서·실패·호출 상한",
            ],
            ["agents/document_agent.py", "텍스트 PDF 우선, 이미지 PDF는 OCR / 원문 검증"],
            [
                "services/financial_calendar.py\nservices/receipt_date.py",
                "기업 일정 입력과 거래 연결 / 예상 입금일 계산",
            ],
            [
                "services/financial_conflict.py\nservices/service_policy.py",
                "세 충돌 규칙 / 고객 서비스·질문·검색 주제",
            ],
            [
                "services/product_catalog.py\nservices/product_vector_store.py",
                "검토한 PDF 구간 / 임베딩·Chroma·해시 검증",
            ],
            [
                "services/financial_retrieval.py\nagents/finance_advisor.py",
                "조건·주제별 검색 / 실제 출처 조합·원문 대조",
            ],
            ["prompts/ · services/report_generator.py", "세 Agent 지시문 / 결과만 배치하는 보고서"],
        ],
        [81, 93],
    )
    p("실제 실행 확인", "heading")
    p(
        f"합성 거래 1건을 gpt-5-nano Flex로 실행했습니다. {result['llm_calls']}회 호출, 입력 {result['input_tokens']:,}토큰·출력 {result['output_tokens']:,}토큰입니다. 자동 확인 {sum(bool(v) for v in verification['checks'].values())}/{len(verification['checks'])}개를 통과했습니다."
    )
    p(
        "실제 OCR·Excel·E5·ChromaDB·보고서가 연결되었습니다. 입금일 10월 21일과 대출 6일·외환 5일·공급자 2일의 일정 차이를 확인했습니다."
    )
    p(
        "자동 테스트의 모의 LLM과 실제 API 검증은 구분합니다. 상세 확인 항목과 최신 실행 정보는 docs/verification-summary.json, 테스트 범위는 docs/example-verification.md에 기록합니다.",
        "small",
    )
    p("남은 범위", "heading")
    p(
        "금융 전문가 검증, 다양한 스캔·계약의 품질 평가, 최신 약관 갱신, 인증·권한·개인정보 통제는 후속 과제입니다. 현업 배포나 일반적인 금융 판단 정확도를 입증한 프로젝트로 설명하지 않습니다.",
        "small",
    )
    output = ROOT / "output/pdf/TradeFlow-MVP-Guide-Legacy-v3.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title="TradeFlow 이전 v3 고정 본문 발췌 MVP 설명서",
        author="TradeFlow",
    )
    footer = partial(_footer, font=font)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    # This authored guide contains no customer data or bank source excerpts.
    (ROOT / "docs/TradeFlow-MVP-Guide-Legacy-v3.pdf").write_bytes(output.read_bytes())
    print(output)


if __name__ == "__main__":
    main()
