# KB TradeFlow Twin v16.0

KB TradeFlow Twin은 Booking, Commercial Invoice, Bill of Lading과 고객 금융일정을 하나의 거래 상태로 연결하는 live 무역금융 멀티에이전트입니다. 사용자의 자연어 요청을 Planning Agent가 작업 목록으로 만들고, Supervisor Agent가 각 작업을 소유한 전문 Agent에 위임합니다. 날짜·금액·충돌·우선순위는 검증된 Tool과 XLSX 규칙이 계산하며, 모델은 계획·라우팅·설명만 담당합니다.

## 핵심 구조

[`langgraph.json`](langgraph.json)은 심사·사용자 진입점을 `chat` 하나로 제한합니다.

| 진입점 | 목적 | 주요 흐름 |
| --- | --- | --- |
| `chat` | 일반 대화와 전체 업무 요청 | Planning ↔ Supervisor 공통 제어, 전문 Agent, Human 확인, 최종 답변 |

`chat`의 공통 제어 흐름은 아래와 같습니다.

```text
__start__
  → planning_agent
      ├─ 일상 대화: 즉시 답변 → __end__
      └─ 업무 요청: 구조화된 Plan 생성
          → supervisor_agent
              → 선택된 specialist_agent (model ↔ tools)
              → supervisor_agent가 Tool 결과 수집
          → planning_agent가 남은 Plan 재확인
              ├─ 다음 작업: supervisor_agent
              ├─ 확인 필요: human_question → human_confirmation → planning_agent
              └─ 완료: 근거 기반 최종 답변 → __end__
```

Planning Agent는 전체 목표를 2~12개의 독립 작업으로 나누고, Supervisor Agent는 현재 작업 하나만 라우팅합니다. 전문 Agent의 결과는 항상 Supervisor를 거쳐 Planning으로 돌아가므로 남은 계획, Tool 결과, Human 응답을 같은 상태에서 재확인할 수 있습니다.

거래 작업 큐는 Chat이 호출하는 내부 structured-trade 경계로 유지됩니다. 별도
`daily_monitoring` graph, 자동 실행 service, 호환 실행 Tool은 제거되었습니다.
사용자는 Chat의 1~4번 메뉴에서 모든 흐름을 시작하며, 2번 메뉴는 금융일정 후보를
선택한 다음 각 거래의 Shipment와 Financial Agent를 호출하고 Critic 검사 후
`MonitoringRun`, 중복 방지 `Alert`, 금일 내부 PDF를 저장합니다.

## 여덟 전문 Agent

| Agent | 책임 | 소유 Tool |
| --- | --- | --- |
| `document_intelligence` | 업로드 문서 분류·필드 추출·Core 검증, 원문 근거와 중간 분석 봉투 저장 | `stage_document_intelligence` |
| `trade_case_manager` | Booking·Invoice·B/L 거래 매칭, 완전성 판정, 수기 확인값과 거래 상태 반영 | `bundle_trade_cases`, `apply_document_field_override`, `commit_trade_cases` |
| `financial_calendar` | 고객 금융일정 XLSX 검증·반영, 회사·거래·이벤트 연결, 모니터링 후보 선택, 충돌 후 명시적 연결 상세 보완 | `validate_financial_calendar`, `import_financial_calendar`, `select_financial_monitoring_candidates`, `update_financial_event_link_details` |
| `shipment_timeline` (Shipment State/Timeline Agent) | Booking 계획과 B/L 실제 상태 분리, 현재 선적 상태 조회, 사용자 지연 제보 기록 | `read_shipment_snapshot`, `record_shipment_delay_scenarios` |
| `financial_exposure` | PaymentTerms Gate, 예상 회수일, 금융일정 충돌, 우선순위와 계산 결과 저장 | `inspect_payment_gate`, `calculate_financial_exposure`, `run_proactive_risk_scan`, `get_financial_risk_snapshot` |
| `product_advisor` | 금융위험·거래 문맥과 상품 후보 매칭, hard filter, KB 상품 PDF 페이지 근거 검색 및 근거 있는 선택지 구성 | `match_product_scenario`, `retrieve_product_evidence`, `search_product_knowledge` |
| `report_writer` | 수동 모니터링 결과 봉인, 하나의 동결된 근거로 고객용·KB 직원용 PDF 생성 | `finalize_manual_monitoring`, `build_briefing_payload`, `render_customer_report`, `render_rm_report` |
| `critic` | 실행 순서, Tool 근거, 숫자 출처, 안전 규칙과 최종 결과 독립 검증 | `review_workflow_evidence` |

Document Intelligence는 실행 시 `stage_document_intelligence` 내부에서
검증된 `field_registry()`를 직접 조회한다. 필드 계약 목록·개수 점검은
내부 `inspect_field_contract_diagnostic` 함수로 유지되며 Agent Tool로
노출하지 않는다.

Tool 소유권은 [`app/agents/supervisor.py`](app/agents/supervisor.py)의 allow-list로 강제됩니다. Supervisor가 다른 Agent를 선택하더라도 실제 실행은 해당 Tool의 정식 소유 Agent로 교정됩니다.

## 세 가지 최종 심사 시연

최종 자료는 [`data/judge_demo_final`](data/judge_demo_final)에 데모별로 분리합니다.
세 시연은 모두 공개 `chat` 그래프와 같은 Agent Server Thread를 사용하므로,
왼쪽 Streamlit 입력과 오른쪽 Studio의 Planning → Supervisor → Agent → Tool 실행을
하나의 기록으로 보여줄 수 있습니다.

### Demo 0. 완전한 3문서 분류·거래 매칭

메뉴 3에서 Booking·Commercial Invoice·B/L 각 1개를 한 번에 올립니다. Document
Intelligence가 세 문서의 종류와 Core 필드를 확인하고, Trade Case Manager가 공통
거래 참조로 `TXN-DEMO0`을 묶고 `CASE-DEMO0`을 구성해
Company·TradeCase·Shipment·DocumentFact·
PaymentObligation에 반영합니다. 세 문서가 완전하므로 Human interrupt 없이 종료하고,
Chat은 분류 3건·거래 1건·DB 반영 1건을 요약한 뒤 금융일정 등록을 다음 선택지로
제시합니다.

### Demo 1. B/L 미수령 거래의 금일 선제 위험 분석

`DEMO1-CO`에는 Booking·Invoice와 금융 이벤트가 이미 등록되어 있고 B/L만 아직
없습니다. 메뉴 2를 누르면 Financial Calendar가 이 거래를 후보로 선정하고,
Shipment State/Timeline → Financial Exposure → Critic → Report Writer 순으로
안전 기준일을 넘긴 위험을 탐지합니다. 사용자가 `충돌 종합보고서 생성`을 선택하고
보고서 생성에 동의하면 Product Advisor가 KB 상품 원문 페이지 근거를 검색하고,
동일한 계산 기준으로 고객용·RM용 PDF를 저장합니다.

### Demo 2. 사전 통보된 9일 선적 지연 영향 분석

`CASE-DEMO2`의 선적이 9일 늦어진다는 제보를 입력합니다. Shipment
State/Timeline은 실제 선적 사실을 덮어쓰지 않고 조건부 지연 시나리오만 저장합니다.
PaymentTerms의 B/L 기준은 Human에게 본선적재일 적용 여부를 확인하고, 충돌 계산 후
거래 대금 의존 범위는 `FULL`/`PARTIAL`로 확인합니다. `PARTIAL`일 때만 금액·통화를
추가 질문합니다. Critic 검증, 상품 근거 검색, 고객용·RM용 보고서 저장 후 UI는
`상담사 연결`/`나중에`를 제시하며 실제 외부 연결은 수행하지 않습니다.

두 보고서는 같은 risk snapshot, 상품 근거, 기준일과 basis version을 사용합니다. 고객용에는 내부 우선순위·직원 메모가 제외되고, KB 직원용에는 상담 준비와 감사 정보를 추가합니다.

## 실제 입력 자산

### 문서·PaymentTerms 계약

- [98개 문서 필드 원본](<kb_doc/BL, Booking, Invoice _필드정리.xlsx>)
- [실행용 문서 필드 계약](data/domain_inputs/active/document_field_dictionary.xlsx)
- [PaymentTerms 검토 원본](kb_doc/PaymentTerms_최종.xlsx)
- [실행용 PaymentTerms 계약](data/domain_inputs/active/payment_terms_cases.xlsx)
- [Booking 원본 양식](kb_doc/booking.pdf)
- [Commercial Invoice 원본 양식](<kb_doc/상업송장(Commercial Invoice).pdf>)
- [Bill of Lading 원본 양식](<kb_doc/선하증권(Bill of Lading).pdf>)
- [최종 Demo 0·1·2 자료와 manifest](data/judge_demo_final/README.md)

### 금융일정·우선순위·브리핑

- [고객 금융일정과 충돌 시나리오](kb_doc/화주ABC_금융일정_충돌시나리오.xlsx)
- [금융일정 우선순위 규칙](kb_doc/금융일정_우선순위_로직.xlsx)
- [고객용·KB 직원용 브리핑 구성](kb_doc/KB_TradeFlow_Twin_브리핑_구성안.xlsx)
- [데이터 스키마 설명](kb_doc/KB_TradeFlow_Twin_개발자전달용_데이터스키마.md)

### KB 상품 근거

- [상황-상품 매칭 분석](kb_doc/KB_금융상품_시나리오_매칭_분석.pdf)
- [구조화 상품 catalog](data/knowledge/product_catalog.json)
- [페이지 단위 OCR index](data/knowledge/product_ocr_index.json)
- [KB 상품 원문 PDF 디렉터리](<kb_doc/KB 금융상품 pdf>)

OCR index는 각 원문 PDF의 SHA-256과 페이지 번호를 보존합니다. 원문 해시가 달라지면 retrieval이 실패하도록 구성해 오래된 근거가 조용히 사용되지 않게 합니다.

## 실행 준비

Python 3.12와 `uv`가 필요합니다.

```bash
cd /Users/chogyeongtae/Desktop/kb_agent
cp .env.example .env
./scripts/bootstrap.sh
```

기존 `.env`가 있으면 덮어쓰지 않습니다. live 실행에는 다음 값이 필요합니다.

```dotenv
TRADEFLOW_MODE=live
OPENAI_API_KEY=...
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=kb-tradeflow-twin-live
```

macOS Desktop 동기화로 가상환경 파일이 비워지는 환경에서는 Desktop 밖의 경로를 지정합니다.

```bash
export TRADEFLOW_VENV_PATH="/absolute/non-icloud/path/kb-tradeflow-venv"
./scripts/bootstrap.sh
```

## 실행 순서

### 1. 최종 Demo 0·1·2 자료 생성·DB 사전 준비

```bash
python scripts/generate_final_demo_portfolio.py
python scripts/prepare_final_demo_portfolio.py --demo all
```

첫 명령은 Demo 0의 완전한 3문서와 Demo 1·2의 Booking·Invoice, 금융일정 seed,
manifest를 생성합니다. 두 번째 명령은 같은 도메인 Service를 사용해 각 demo batch를
분석·거래화하고 금융일정을 DB에 반영합니다. 두 명령 모두 모델 API를 호출하지
않습니다.

### 2. 전체 데모 한 번에 실행

LangGraph Dev가 준비된 뒤 FastAPI와 Streamlit을 순서대로 시작하려면:

```bash
./scripts/start_all_demo.sh
```

저비용 Live 연습 프로필로 세 서비스를 함께 시작하려면:

```bash
./scripts/start_practice.sh all
```

### 3. 개별 실행

먼저 LangGraph Dev를 실행합니다.

```bash
./scripts/start_langgraph_dev.sh
```

준비가 완료된 뒤 다른 터미널에서 FastAPI와 Streamlit을 실행합니다.

한 터미널에서 함께 실행하려면:

```bash
./scripts/start_demo.sh
```

각각 실행하려면:

```bash
./scripts/start_api.sh
./scripts/start_ui.sh
```

- Streamlit: `http://127.0.0.1:8501`
- FastAPI: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`

Streamlit에서 `Demo 0`, `Demo 1`, `Demo 2` 중 하나를 선택하면 해당 demo의
고정 회사·batch·case 문맥이 같은 Agent Server Thread에 설정됩니다. Demo 0은 세
PDF를 업로드해 분류·거래 매칭을 보여주고, Demo 1·2는 사전 준비된 거래와 금융일정을
사용해 각각 금일 위험 분석과 사용자 제보 지연 분석을 진행합니다.

### 4. LangGraph Studio 연결

```bash
./scripts/start_langgraph_dev.sh
```

- Local LangGraph API: `http://127.0.0.1:2024`
- API 문서: `http://127.0.0.1:2024/docs`
- Studio: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

Studio에는 공개 그래프 `chat` 하나만 노출됩니다. `chat`을 선택하면 Planning,
Supervisor, 선택된 Agent, Tool 호출, Human interrupt, 최종 응답을 한 실행 기록에서
확인할 수 있습니다. 거래 실행과 금일 수동 모니터링도 모두 이 Chat 그래프에서
시작합니다. 모니터링 전용 standalone graph나 별도 실행 endpoint는 없습니다.

Streamlit Chat을 열면 `:2024` Agent Server에 실제 Thread가 생성되고 전체 Thread ID가
화면에 표시됩니다. 이 ID를 복사해 Studio에서 같은 Thread를 선택하면, 왼쪽
Streamlit에서 보낸 질문의 Planning → Supervisor → Agent 실행을 오른쪽 Studio에서
같은 실행으로 확인할 수 있습니다. `새 대화`는 새 Agent Server Thread를 만듭니다.

### 연습용 저비용 Live 프로필

데모용 `.env`는 변경하지 않습니다. 연습할 때만
[`config/practice.env`](config/practice.env)의 혼합 모델 프로필을 적용합니다.

- Planning·Supervisor·Financial·Critic: `gpt-5.6-terra`
- Document·Shipment·Product·Report Writer: `gpt-5.6-luna`
- reasoning effort: `low`

LangGraph Dev 연습:

```bash
./scripts/start_practice.sh langgraph
```

Streamlit과 FastAPI 연습:

```bash
./scripts/start_practice.sh chat
```

API와 UI를 별도 터미널에서 실행할 때:

```bash
./scripts/start_practice.sh api
./scripts/start_practice.sh ui
```

연습 프로필에는 API 키가 들어 있지 않습니다. 키는 기존 `.env`에서 읽고,
실행 프로세스의 모델 관련 환경변수만 임시로 덮어씁니다. 일반 실행 명령으로
다시 시작하면 원래 데모 모델 설정이 그대로 적용됩니다.

### Chat 시연 질문

```text
Demo 0) 3
        업로드한 Booking·Invoice·B/L을 분류하고 같은 거래로 묶어 등록해줘.

Demo 1) 2
        2026-08-20 기준 금일 금융위험 거래를 우선순위 순으로 분석해줘.

Demo 2) CASE-DEMO2 거래의 선적이 9일 늦어질 예정입니다.
        금융일정 충돌과 위험순위를 분석해줘.
```

## Human interrupt와 resume

확인이 필요하면 `chat` API는 다음 구조를 반환합니다.

```json
{
  "status": "HUMAN_REQUIRED",
  "thread_id": "trade-live-001",
  "confirmation_id": "CONF-...",
  "issue": {
    "issue_code": "REPORT_GENERATION_CONSENT",
    "prompt": "동일한 근거로 고객용·RM용 보고서를 생성할까요?",
    "response_key": "report_consent",
    "value_type": "boolean",
    "allowed_values": [true, false]
  }
}
```

응답할 때는 interrupt가 준 `issue_code`를 그대로 돌려보내야 합니다.

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/threads/trade-live-001/resume" \
  -H "Content-Type: application/json" \
  -d '{"issue_code":"REPORT_GENERATION_CONSENT","value":true}'
```

필드 확인은 문자열, 지연 일수 목록은 정수 배열로 전달합니다.

```json
{"issue_code": "DELAY_DAYS_REQUIRED", "value": [9]}
```

Studio의 Interrupts 화면에서도 동일한 `issue_code`와 `value` JSON으로 재개합니다. 질문, 응답, 실행 thread는 `Confirmation` 감사 기록에 남습니다.

## 안전 원칙

- **문서 누락과 필드 누락은 다릅니다.** B/L 문서 자체가 없으면 `AWAITING_DOCUMENT`이며, 존재하지 않는 문서의 필드값을 수기로 묻지 않습니다. 문서가 있고 필수 필드 하나만 비어 있을 때만 정확한 필드를 질문합니다.
- **B/L 부재는 미출항 증거가 아닙니다.** B/L 번호나 실제 선적 근거가 없으면 상태를 `UNKNOWN`으로 표시하며 미선적·미출항으로 단정하지 않습니다.
- **모델이 금융 계산을 하지 않습니다.** PaymentTerms 해석 결과, 기준일, tenor, 금융 이벤트와 우선순위 XLSX를 검증된 Tool이 읽고 날짜·금액·충돌을 계산합니다.
- **Payment Gate를 통과하기 전 계산하지 않습니다.** 검증되지 않은 기준일이나 지원하지 않는 결제조건은 계산을 차단하고 필요한 정확한 값만 질문합니다.
- **원본과 가상 시나리오를 분리합니다.** 지연 분석과 지연 시나리오는 별도 `CalculationResult`로 저장하며 문서 원문과 실제 Shipment 상태를 변경하지 않습니다.
- **Tool이 DB 변경을 소유합니다.** Planning, Supervisor와 모델은 직접 DB commit을 수행하지 않습니다.
- **상품은 원문 페이지 근거가 있어야 제시합니다.** hard filter를 통과하고 해시 검증된 PDF citation이 있는 상품만 옵션으로 구성합니다.
- **두 보고서는 동일한 근거를 사용합니다.** 한 번 동결한 risk snapshot과 상품 근거로 고객용·KB 직원용 보고서를 함께 만들며 명시적 동의가 있어야 생성합니다. RM 연락은 UI 제안만 하고 외부 전송은 수행하지 않습니다.
- **비밀값과 내부 추론은 노출하지 않습니다.** Debug Trace에는 공개 가능한 Node, Agent, Tool, 입력·출력 요약, call ID와 실행시간만 남깁니다.

## 출력 보고서

동의가 완료되면 다음 파일이 생성됩니다.

```text
data/reports/{case_id}_customer.pdf
data/reports/{case_id}_rm.pdf
```

- 고객용: 거래 개요, 선적 확인사항, 금융 위험, 고객이 수행할 다음 단계, 조건부 상품 정보
- KB 직원용: 고객용과 동일한 basis에 내부 우선순위, 근거 ID, 확인 질문, 상담 준비·인계 정보를 추가

Streamlit Chat과 Case Reports 화면에서 두 PDF를 내려받을 수 있습니다.

## 검증 명령

최종 숫자를 README에 고정하지 않고 아래 명령의 현재 결과를 기준으로 판단합니다.

```bash
python scripts/generate_final_demo_portfolio.py
python scripts/prepare_final_demo_portfolio.py --demo all
python scripts/verify_integrations.py

ruff check .
ruff format --check .
mypy app scripts
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

## 주요 파일

- [LangGraph 진입점 설정](langgraph.json)
- [공통 Planning-Supervisor 제어](app/graphs/common_control.py)
- [공개 Chat과 내부 structured-trade graph 조립](app/graphs/compiled.py)
- [공통 상태](app/graphs/state.py)
- [요청 기반 계획 생성](app/services/workflow_planning.py)
- [구현 Phase 기록](IMPLEMENTATION_PHASE_LOG.md)
- [원천 파일 manifest](data/source_manifest.json)
- [최종 Demo 0·1·2 자료 안내](data/judge_demo_final/README.md)
- [최종 Demo manifest](data/judge_demo_final/manifest.json)
- [최종 시나리오 가이드 PDF](output/pdf/KB_TradeFlow_Twin_Demo_0_1_2_시나리오_가이드.pdf)
