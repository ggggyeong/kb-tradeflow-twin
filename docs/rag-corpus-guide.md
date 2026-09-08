# RAG 자료 선정·검색 기준

2026-09-08 구현 기준. 자료를 많이 넣기보다 **서비스에 필요한 본문을 왜 선택했고 언제 검색하는지** 설명할 수 있게 구성했다.

## 현재 활성 자료

| 공식 원문 | PDF 페이지 | 본문 주제 |
| --- | --- | --- |
| [신한은행 기업대출 상품설명서](https://img.shinhan.com/sbank2016/form/20110131618000010051WF00001000000001.PDF?1768922540586=) | 12·17·18·19 | 운전자금 용도·거래 조건, 상환 방법·연체·연장 |
| [HSBC 수출입 및 보증거래 설명서](https://www.hsbc.co.kr/-/media/korea/attachments/korea-kr/cmb-agreement/biz_agreement_029_kr.pdf) | 1 | 수출환어음 매입 비용 |
| [HSBC 수출채권매입거래약정서](https://www.hsbc.co.kr/-/media/korea/attachments/korea-kr/cmb-agreement/biz_agreement_036_kr.pdf) | 4·5·7 | 매입 선결 조건·환매 의무·제출 서류 |
| [중국은행 선물환 고객매도 설명서](https://pic.bankofchina.com/bocappd/korea/202604/P020260427380685258568.pdf) | 3·4·5 | 매도 계약·환율 손실·중도해지 |

HSBC는 [공식 약관 목록](https://www.hsbc.co.kr/ko-kr/cmb-agreement), 중국은행은 [공식 게시글](https://www.bankofchina.com/kr/kr/bocinfo/bi4/202205/t20220505_21084724.html)에서 원문 연결을 확인했다.

총 PDF 4개·48페이지 중 **11페이지의 12개 본문 구간**을 색인했다. 현재 각 구간은 800자 미만이다. 신한은행 본문이 두 충돌 코드에 각각 연결되어 Chroma 레코드는 17개다. 레코드 수는 상품 수가 아니다.

## 어떤 상황에서 이어지나요?

| 서비스 | 허용 주제 | 별도 조건 |
| --- | --- | --- |
| 대출 상환 점검 | LOAN_REPAYMENT / LOAN_OVERDUE / LOAN_EXTENSION | 신규 운전자금 조달 본문과 구분 |
| 기존 선물환 결제 점검 | FX_SETTLEMENT / FX_RISK / FX_CLOSEOUT | 수출·매도 방향·같은 통화·확인된 거래 연결 |
| 공급자 지급 준비 | FUNDING_PURPOSE / FUNDING_ELIGIBILITY | 실제 지급 목적과 이용 조건은 추가 확인 |
| 공급자 지급 준비의 수출채권 정보 | RECEIVABLE_TERMS / DOCUMENTS / RECOURSE / COST 접두 주제 | EXPORT + export_receivable_confirmed=true |

이것은 **자료 라우팅 조건**이지 대출 자격 심사나 최적 상품 추천이 아니다. HSBC 약정서의 채무자 미지급 등에 따른 환매 의무를 포함하므로 무조건 비소구로 설명하지 않는다. 선물환 자료는 기존 계약 확인용이며 신규 가입·자동 연장 해결책이 아니다. 다른 은행 자료가 실제 계약을 대신하지 않는다.

## 페이지 전체가 아니라 본문 구간

`data/knowledge/product_catalog.json`의 각 자료에는 다음을 기록한다.

- 출처: `source_url`, `source_date`, `reviewed_on`, `reviewed_sha256`
- 활성 상태: `review_status=REVIEWED`, `enabled=true`
- 자료 연결: `scenario_codes`, `trade_directions`, 지급 목적·수출채권 확인 조건
- 구간: `sections[].section_id`, `topic`, `page`, `start_anchor`, `end_anchor`

시작 문구를 포함하고 끝 문구 직전까지 추출한다. 공백은 정규화한다. 경계가 누락·중복되거나 순서가 뒤집히면 색인을 만들지 않는다. `include_pages`는 구간의 실제 PDF 페이지와 일치해야 한다. PDF에 인쇄된 쪽 번호와 다를 수 있다.

한 페이지에 수입금융·보증·금리 예시가 함께 있어도 선택 구간 밖이면 검색하지 않는다. 미지정 구간은 레거시 색인 테스트에서 GENERAL로 처리할 수 있지만, 현재 금융 서비스 검색에서는 GENERAL을 허용하지 않는다.

## 저장과 검색

```text
공식 PDF → 해시 확인 → 검토 구간 추출 → 구간 안에서 분할 → 로컬 E5 → Chroma
확인된 충돌 → 서비스 정책 → 자료 조건 → 코드+자료 ID+주제 필터 → 주제별 본문 1개
```

`intfloat/multilingual-e5-small`을 CPU에서 사용한다. 문서는 `passage:`, 질문은 `query:`를 붙여 384차원으로 임베딩한다. 최대 800자·중첩 100자로 구간 안에서만 분할한다. 고객 문서·엑셀은 공용 상품 DB에 넣지 않는다.

컬렉션은 `trade_finance_evidence`, 로컬 경로는 `data/knowledge/chroma_products/`다. 본문·벡터·자료 ID·충돌 코드·주제·구간 ID·파일·페이지·해시·URL·검토일을 저장한다. 자료명·은행은 카탈로그의 검토값을 사용한다.

LLM에는 검색 본문과 서비스 목적을 제공하고 핵심 원문을 선택하게 한다. 자유로운 금융 요약은 생성하지 않는다. 출처 ID 조합을 도구 규격으로 제한하고, 코드에서 파일·해시·페이지·구간·출처 누락·핵심 원문 문자열 일치를 대조한다. 선택 문장의 관련성·완전성과 실제 적용 여부는 사람이 원문·개별 계약과 함께 확인해야 한다.

## 재현과 갱신

```bash
uv run python scripts/fetch_product_pdfs.py
uv run python scripts/build_product_vector_index.py --dry-run
uv run python scripts/build_product_vector_index.py
```

다운로드는 고정 공식 URL을 사용하며 기존 파일은 덮어쓰지 않는다. Agent 실행 중 웹 검색이나 상품 추가는 없다. 현재 4개 PDF는 네이티브 텍스트를 사용한다. 이미지 PDF를 추가하면 OCR이 필요하다. 색인은 유료 LLM을 호출하지 않는다.

`--dry-run`은 자료 수·선택 페이지·텍스트 유무만 확인한다. 구간 경계 검증은 실제 corpus 생성 단계에서 수행한다. 원문이 갱신되면 해시만 고치지 말고 본문·조건·구간 경계를 다시 읽어야 한다.

manifest v3가 카탈로그·원본·모델·레코드와 일치하는지 확인한다. 전체 임베딩 계산 후 색인 교체를 시작하고 중단 시에는 불완전한 색인을 쓰지 않는다. 운영 중 동시 색인 교체는 지원 범위 밖이다.

## 검색을 보류하는 경우

충돌 없음·입력 미확인에는 검색하지 않는다. 조건에 맞는 검토 자료나 해당 주제 본문이 없으면 설명을 보류한다. PDF·카탈로그가 색인과 달라져도 재검토를 요청한다. 관련 없는 상품으로 자동 대체하지 않는다.

## 공개 범위

공개 대상은 공식 링크·선정 이유·구간 메타데이터·코드·합성 입력·직접 작성한 설명서다. 원본 PDF·모델·ChromaDB·실행 로그·은행 원문 발췌 보고서는 공개하지 않는다.

중국은행 PDF 6쪽은 제3자 인용·배포에 사전 동의를 요구한다. 다른 PDF도 공개 다운로드가 재배포 허가를 뜻하지 않는다. 기존 KB 자료는 현재 검색에서 비활성화했다.

REVIEWED는 개발 단계의 검토 기록이며 금융기관·전문가 승인 표시가 아니다. 외부 API에는 합성 데이터만 사용했다. `store=False`도 공급자의 무보관 보증으로 해석하지 않는다.
