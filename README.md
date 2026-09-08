# TradeFlow · 무역금융 업무 검토 AI Agent

**무역서류 확인 → 금융일정 충돌 탐지 → 상황별 금융정보·근거 → 검토 보고서**

해상무역의 여러 서류에 흩어진 날짜·금액과 금융일정을 함께 분석해, 수출대금 입금 전에 지급·상환 일정이 돌아오는 위험을 찾습니다. 충돌이 확인되면 공식 금융자료를 검색하여 **상황에 필요한 조건·위험·확인사항을 근거와 함께 설명**합니다.

[서비스 설계](docs/service-mvp.md) · [자료 선정 이유](docs/rag-corpus-guide.md) · [실제 검증 기록](docs/example-verification.md)

> Python · LangGraph · OpenAI Tool Calling · PDF/OCR · Excel · ChromaDB RAG
> 합성 거래로 실제 LLM·OCR·엑셀·검색·보고서 연결을 확인한 포트폴리오입니다. 실제 금융기관의 승인이나 금융 판단 정확도를 보증하지 않습니다.

## 무엇을 만들었나요?

| 역할 | 수행하는 일 |
| --- | --- |
| Supervisor | 요청에 필요한 작업을 계획하고 허용된 순서로 실행 관리 |
| Document Agent | PDF에서 날짜·금액·통화·문서번호 추출, 원문·페이지 검증 |
| Finance Advisor Agent | 엑셀 거래 연결, 입금일·일정 비교, 서비스별 금융자료 검색·설명 |
| Report Generator 도구 | 상황·근거·추가 질문·보류 사유를 PDF에 배치 |

별도 Planner는 두지 않았습니다. Supervisor가 계획과 실행 관리를 함께 맡고, 금융충돌 계산과 RAG는 Finance Agent의 도구로 묶었습니다.

```text
요청 + 거래 ID + 무역서류 PDF + 금융일정 Excel
                       │
             Supervisor: 계획·실행 관리
                       │
             Document Agent: PDF/OCR → 필드 검증
                       │
             Finance Advisor Agent
               ├─ 거래 연결 → 예상 입금일 → 날짜 규칙 비교
               ├─ 충돌 코드 → 필요한 정보에 관한 검색 질문
               └─ 자료군의 여러 본문에서 Chroma 검색 → 근거를 인용한 설명
                       │
             상황별 서비스 카드 → 검토용 PDF
```

LLM이 자유롭게 도구를 선택하는 범용 시스템이 아닙니다. LangGraph가 상태를 관리하고 코드가 다음 단계·도구 인자·호출 상한을 제한하는 **업무 흐름형 Agent**입니다.

## 세 충돌을 어떤 서비스로 연결하나요?

| 확인된 상황 | 고객에게 제공하는 정보 | 하지 않는 일 |
| --- | --- | --- |
| 입금 전 대출 만기 | 기존 대출의 상환·연체·연장 상담 관련 정보, 잔액·상환 계획 확인 질문 | 대환대출 자동 추천 |
| 외화 입금 전 선물환 매도 결제 | 기존 계약의 결제 의무·환율 손실·중도해지 유의사항 | 신규 파생상품·자동 연장 제안 |
| 입금 전 공급자 지급 | 추가 자금이 필요한 경우의 비용·유의사항, 수출채권 보유 입력 시 매입 조건·서류·환매 의무 | 실제 부족액·자금조달 가능 여부 확정 |

분류는 코드의 날짜 비교로 수행합니다. 입금이 늦으면 `CONFLICT`, 같은 날이거나 입력이 미확인이면 `REVIEW_REQUIRED`, 입금이 먼저면 날짜 기준 `NO_CONFLICT`입니다. 선물환은 동일 통화·매도 방향·거래 연결도 확인합니다.

**일정 차이는 실제 자금 부족액이 아닙니다.** 잔액·다른 입금·환산·은행 휴일은 반영하지 않습니다. 대출 승인·송금·계약 변경은 하지 않습니다.

## RAG에는 무엇을 저장하나요?

공식 PDF **3개·전체 41페이지 중 24페이지**를 검색 대상으로 사용합니다. 질문별 정답 문구를 지정하지 않고, 고객용 설명·계약 본문을 문단·조항 단위로 나누어 색인합니다. 중복 사본·빈 서식은 제외합니다.

| 자료 | PDF 페이지 | 사용하는 본문 |
| --- | --- | --- |
| 신한은행 기업대출 설명서 | 12–21 | 고객용 설명 전체: 상환·비용·연장·갱신 관련 정보 |
| HSBC 수출채권할인 기본 계약 | 2–9 | 정의·매입 조건·서류·계약 자체의 비용·환매 의무 |
| 중국은행 선물환 고객매도 설명서 | 1–6 | 결제 의무·위험·중도해지와 이어지는 정산 조건 |

```text
사전 준비: 공식 PDF 검토 → 문단·조항 분할 → E5 임베딩 → ChromaDB
실행: 충돌 확인 → 검색 질문 → 거래에 맞는 자료군 → 유사도 검색 → 근거 기반 설명
```

- 로컬 `intfloat/multilingual-e5-small`(384차원)을 사용합니다. 유료 임베딩 API는 쓰지 않습니다.
- 문단·항목 경계와 실제 E5 토큰 한도를 고려해 분할합니다. 임베딩 과정의 조용한 입력 잘림을 허용하지 않습니다.
- 본문·벡터와 함께 자료 ID, 거래 범위, 파일명·페이지·해시·원문 URL을 저장합니다.
- Chroma에는 **충돌 및 거래 조건에 맞는 자료군**만 제한합니다. 실행 중 정답 페이지·세부 주제를 지정하지 않고 질문마다 본문 최대 3개를 검색합니다.
- `retrieval_trace`에 실제 검색문·후보 수·검색 결과·점수를 기록합니다. 현재 본문/레코드 수는 재생성된 `product_vector_manifest.json`으로 확인합니다. 두 충돌에 쓰이는 같은 본문은 레코드가 중복되므로 고유 본문 수와 구분합니다.
- 재색인할 때 새 컬렉션을 만들고 검증한 뒤 활성 색인을 바꿉니다. 이전 컬렉션은 보존합니다. 실행 중인 API는 재색인 후 재시작해야 합니다.
- 수출채권 보유가 미확인이면 채권 관련 자료만 제외하고, 공급자 지급의 기본 운영자금 정보 검색은 유지합니다. 거래 조건에 맞는 자료가 없거나 PDF·색인이 변경됐으면 해당 설명을 보류하며, 관련 없는 상품으로 대체하지 않습니다.
- LLM은 먼저 충돌별 핵심 질문 2~3개에 대해 실제 검색 후보 중 **근거 ID 또는 `null`만 선택**합니다. 설명은 아직 작성하지 않습니다. 다음 단계는 질문마다 별도 호출로 **그 질문과 선택한 본문 하나만** 읽고 설명합니다. 다른 후보·거래 상황·기존 초안을 함께 주지 않아 설명과 출처가 섞이는 문제를 줄입니다.
- 코드는 선택한 **본문 전체·은행명·페이지**를 붙입니다. 생성 단계에서 검색 청크를 다시 작은 문장으로 자르지 않습니다. 근거가 부족하거나 선택·설명 호출이 실패하면 해당 설명을 보류하며, 답변은 한 문장·최대 220자로 제한합니다. 출처 검사와 단계 분리가 설명의 의미 정확성이나 실제 계약 적용 여부를 보증하지는 않습니다.

이전의 `충돌 + 특정 PDF + 주제 → 후보 1개` 방식은 실제 검색 선택이 거의 없는 고정 조회였습니다. 현재는 동일 자료군에서도 질문에 따라 다른 본문을 선택하는지 로컬 검색 테스트로 확인합니다. 수출환어음 환가료를 수출채권할인 비용으로 혼용하던 공통 설명서 연결도 제외했습니다.

[카탈로그](data/knowledge/product_catalog.json)의 `REVIEWED`는 개발 단계에서 원문을 검토한 상태입니다. 은행·전문가 승인이나 최신 가입 자격을 뜻하지 않습니다. 공개 안내문은 고객의 개별 계약을 대신하지 않습니다.

## AI·코드·사람의 책임

| 담당 | 책임 |
| --- | --- |
| AI | 문서 필드 후보 해석, 검색된 본문을 근거로 쉬운 설명 작성 |
| 코드 | 날짜 계산, 서비스 선택, 검색 필터, 출처·원문 검증, 호출 제어 |
| 사람 | 문서 의미·거래 연결·실제 계약·상품 조건 최종 확인 |

보고서는 **일정 충돌 / 자료에 묻는 질문과 생성 답변 / 원문·출처 / 고객에게 묻는 추가 확인 사항**을 구분합니다. 금융 설명은 반드시 근거가 있는 답변 경로로만 제출하고, 자유 경고문으로 별도 금융 설명을 덧붙이지 않습니다. 고객 확인 사항은 `service_policy.py`의 고정 체크리스트이며, 답변을 받아 재분석하는 대화 기능은 아닙니다.

Document Agent는 텍스트 PDF를 먼저 읽고 이미지 PDF는 RapidOCR로 인식합니다. PaddleOCR는 선택 가능한 대안입니다. 고객 PDF·엑셀은 공용 상품 DB에 넣지 않습니다.

## 입력 예시

엑셀에는 `1.금융이벤트`, `2.거래연결`, `3.거래정보`를 담습니다. 거래 ID와 문서번호로 해당 거래만 연결합니다. 금융일정은 PDF 설명서에서 추측하지 않습니다.

[합성 입력](data/fixtures/tradeflow_example/request.json)은 Booking·송장·B/L과 금융일정 엑셀을 사용합니다. 상업송장의 이미지 전용 사본으로 실제 OCR 경로도 확인합니다.

| 예상 입금일 | 비교 일정 | 일정 차이 |
| --- | --- | --- |
| 2026-10-21 | 대출 만기 10-15 | 6일 |
| 2026-10-21 | 선물환 매도 결제 10-16 | 5일 |
| 2026-10-21 | 국내 공급자 지급 10-19 | 2일 |

송장의 명시적 조건인 **실제 선적일 9월 21일 + 30일**로 계산합니다. B/L 발행일 9월 22일·Booking 예정 출항일 9월 18일은 대신 사용하지 않습니다. 지원하지 않는 결제조건은 확인을 요청합니다. 한 요청은 거래 1건·PDF 최대 3개·금융일정 최대 3개입니다.

## 실행하기

Python 3.12·uv를 사용합니다. 경로에 콜론(:)이 있으면 외부 가상환경을 쓰거나 콜론이 없는 경로에 복제합니다.

```bash
uv sync --extra dev --extra ocr
# .env.example을 참고해 로컬 .env에 새 키 설정
uv run python scripts/fetch_product_pdfs.py
uv run python scripts/build_product_vector_index.py --dry-run
uv run python scripts/build_product_vector_index.py
uv run python scripts/run_example.py        # 준비 확인만, 유료 호출 없음
uv run python scripts/verify_rag.py         # 실제 로컬 검색문·후보·본문 확인, 유료 호출 없음
uv run python scripts/verify_rag.py --live  # Finance Agent 단독, 유료 최대 13회 호출
uv run python scripts/run_example.py --live # 실제 유료 실행
```

예시 실행은 `gpt-5-nano` Flex, 전체 최대 20회 호출·각 출력 최대 6,000토큰을 사용합니다. 자동 재시도·상위 모델·일반 요금 등급 전환은 없습니다. RAG 단독 검증은 최대 13회이며 근거 선택과 질문별 단일 본문 설명 모두 `high` 추론을 사용합니다. 실제 토큰 사용량에는 추론도 포함하며, 출력 상한이 곧 사용량은 아닙니다. 자료·카탈로그가 바뀌면 재검토·재색인이 필요합니다. 캐시 모델만 쓸 때는 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`을 설정합니다.

결과는 `output/example/live-result.json`, 보고서는 `output/pdf/tradeflow-example-live.pdf`입니다. 예시용 실행 기록에는 키·비공개 추론을 저장하지 않습니다. 합성 데이터 전용 로그를 실제 고객에게 그대로 사용하지 않습니다.

로컬 API는 `uv run uvicorn app.api.main:app --host 127.0.0.1 --port 8000`으로 실행합니다. 분석 엔드포인트는 `POST /api/portfolio/analyze`입니다. 인증·권한이 없는 포트폴리오 API를 외부에 공개하지 않습니다.

## 코드 읽는 순서

1. [service_policy.py](app/services/service_policy.py): 고객 서비스·확인 질문·정보 검색 질문
2. [portfolio_pipeline.py](app/services/portfolio_pipeline.py): 전체 실행 흐름과 도구 통제
3. [document_agent.py](app/agents/document_agent.py): PDF 해석·원문 검증
4. [financial_calendar.py](app/services/financial_calendar.py) · [receipt_date.py](app/services/receipt_date.py) · [financial_conflict.py](app/services/financial_conflict.py): 입력·날짜 규칙
5. [financial_retrieval.py](app/services/financial_retrieval.py) · [product_vector_store.py](app/services/product_vector_store.py): 자료군 내 의미 검색과 검색 기록
6. [finance_advisor.py](app/agents/finance_advisor.py) · [prompts/](app/prompts/): 근거 설명과 Agent 지시문
7. [report_generator.py](app/services/report_generator.py): 서비스 카드의 PDF 출력

## 검증과 한계

[자동 테스트·실제 API 검증 기록](docs/example-verification.md)과 [원문·키를 제외한 실행 요약](docs/verification-summary.json)을 공개합니다. 자동 테스트의 모의 LLM과 실제 API 실행은 구분합니다.

2026-09-08 기준 자동 테스트 205개 통과. 마지막 실제 실행은 세 충돌의 검색·보고서 연결을 확인했지만, 핵심 답변 8개 중 7개 생성·1개 토큰 상한 보류인 **부분 결과**입니다. 생성된 설명에도 관련성·범위 표현상 한계가 있어 완전 성공이나 금융 정확성 보장으로 표시하지 않습니다.

```bash
uv run python -m pytest -q
# 모델·공식 PDF 확보 및 색인 구축 후, 실제 OCR/E5/Chroma 통합 테스트 포함
TRADEFLOW_LOCAL_MODELS=1 HF_HUB_OFFLINE=1 uv run python -m pytest -q
uv run ruff check app scripts tests
uv run mypy app
```

한 합성 거래의 연결 확인을 일반적인 정확도·업무 절감률로 표현하지 않습니다. 금융 전문가 검증, 다양한 계약·스캔 품질 평가, 최신 약관 갱신, 인증·권한·개인정보 통제는 후속 과제입니다.

현재 활성 RAG의 은행 원본 PDF·ChromaDB·모델 캐시·키·발췌 보고서는 이번 업데이트에 포함하지 않습니다. 기존 저장소의 KB 자료와 과거 샘플은 보존하되 활성 검색에서 제외했습니다. [공식 자료 링크와 이용 주의사항](docs/rag-corpus-guide.md)
