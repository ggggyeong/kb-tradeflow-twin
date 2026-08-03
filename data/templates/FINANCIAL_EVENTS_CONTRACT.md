# 금융일정 2-Sheet XLSX 입력 계약

금융일정은 로그인 세션의 `company_id` 아래 이미 등록된 TradeCase에만
연결합니다. XLSX 안에서는 회사·거래·선적·지급조건을 새로 만들지 않습니다.

## Sheet 1: `1.금융이벤트`

정확한 컬럼 순서:

1. `event_id`
2. `event_type_code`
3. `event_date`
4. `amount`
5. `currency`
6. `financial_institution`

데모 입력 예:

| event_id | event_type_code | event_date | amount | currency | financial_institution |
|---|---|---:|---:|---|---|
| EVT-DEMO-001 | WORKING_CAPITAL_LOAN_MATURITY | 2026-08-20 | 50000 | USD | KB국민은행 |
| EVT-DEMO-002 | SUPPLIER_PAYMENT | 2026-08-16 | 35000 | USD | KB국민은행 |
| EVT-DEMO-003 | FX_FORWARD_MATURITY | 2026-08-18 | 42000 | USD | KB국민은행 |

## Sheet 2: `2.거래연결`

정확한 컬럼 순서:

1. `transaction_id`
2. `event_id`
3. `link_type`
4. `link_status`

데모 입력 예:

| transaction_id | event_id | link_type | link_status |
|---|---|---|---|
| TXN-DEMO-001 | EVT-DEMO-001 | EXPECTED_EXPORT_RECEIPT | CONFIRMED |
| TXN-DEMO-002 | EVT-DEMO-002 | EXPECTED_EXPORT_RECEIPT | CONFIRMED |
| TXN-DEMO-003 | EVT-DEMO-003 | EXPECTED_EXPORT_RECEIPT | CONFIRMED |

## 시스템이 결정하는 값

- `company_id`: 로그인 세션
- `case_id`: `(company_id, transaction_id)`로 기존 TradeCase 조회
- `transaction_event_link_id`: 회사·거래·이벤트 조합의 안정적 해시
- `event_name`: `event_type_code`의 검토된 표준명
- `is_kb_contract`: `financial_institution`의 결정론적 은행명 판정
- `dependency_scope`: 최초 `UNKNOWN`
- `linked_amount`, `linked_currency`: 최초 `NULL`; 충돌 분석에 꼭 필요할 때만 Human 확인

검증 Tool은 읽기 전용입니다. Import Tool은 `FinancialEvent`와
`TransactionFinancialEventLink`만 멱등 저장합니다.

## 충돌 후 optional 연결 상세 보완

`dependency_scope`, `linked_amount`, `linked_currency`는 XLSX 컬럼이 아닙니다.
Financial Exposure가 실제 날짜 충돌에서 `FINANCIAL_LINK_DETAILS_REQUIRED`를 반환한
경우에만 다음 순서로 보완합니다.

1. `dependency_scope=UNKNOWN`이면 Human에게 `FULL` 또는 `PARTIAL`을 확인합니다.
2. `FULL`이면 금액·통화를 추가로 요구하지 않습니다.
3. `PARTIAL`이면 Human에게 `linked_amount`와 3자리 `linked_currency`를 함께 확인합니다.
4. Financial Calendar의 `update_financial_event_link_details`가 세션의
   `company_id`와 canonical `case_id`·`transaction_id`·link ID 소유권을 재검증해 저장합니다.
5. Financial Exposure가 새 immutable CalculationResult를 계산·저장합니다.

Scheduled scan에서는 이 항목을 `data_actions`로 남겨 무인 실행을 막지 않으며,
사용자 제보 지연 Chat 흐름에서는 같은 thread의 typed Human interrupt로 보완합니다.
