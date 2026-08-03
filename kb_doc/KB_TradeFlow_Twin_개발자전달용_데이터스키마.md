# KB TradeFlow Twin — 개발자 전달용 데이터 스키마

브리핑 구성안(`KB_TradeFlow_Twin_브리핑_구성안.xlsx`)을 실제 Agent 프롬프트/백엔드 출력 스키마로 옮기기 위한 기술 부록입니다. 필드명(key), 허용값, 계산 기준을 정의합니다. 문구·라벨의 "왜"는 브리핑 구성안 문서를, 판정 로직 자체는 `화주ABC_금융일정_충돌시나리오_v8.xlsx`·`금융일정_우선순위_로직_v3.xlsx`를 참고하세요.

---

## 1. 공통 계산 기준

| 항목 | 정의 |
|---|---|
| `today_reference` | 모든 `days_until_event` 계산 및 "30일 이내" 조회의 기준일. 운영 환경에서는 시스템 조회 시점의 당일 날짜(서버 Today). 시연/테스트 환경에서는 B/L 발행일 또는 사용자가 지정한 임의 기준일(`simulation_reference_date`)로 대체 가능. |
| `date_format` | ISO-8601 `YYYY-MM-DD` |
| `day_count` | Calendar Days (달력일 기준, 영업일 아님) |

---

## 2. 고객용 브리핑 출력 스키마 (`customer_brief`)

```json
{
  "trade_id": "TXN003",
  "company_id": "A",
  "buyer_name": "SAKURA ELECTRONICS CO., LTD",
  "port_of_loading": "BUSAN",
  "port_of_discharge": "LOS ANGELES",

  "shipment_status": "DELAYED",          // ON_TIME | DELAYED
  "delay_days": 9,

  "planned_payment_due_date": "2026-09-29",
  "revised_payment_due_date": "2026-10-08",
  "payment_due_date_change_days": 8,

  "has_conflicts": true,
  "data_action_required": false,          // true면 아래 conflict_signals와 별도로 배너만 노출
  "data_action_message": null,            // 예: "결제조건의 기준일(anchor)이 아직 확인되지 않아 지급기준일을 계산할 수 없습니다."

  "conflict_signals": [
    {
      "event_id": "FE001",
      "event_type_code": "SUPPLIER_PAYMENT",
      "display_label": "확정된 충돌",       // "확정된 충돌" | "긴급 확인 필요"
      "same_day_flag": false,
      "lead_days": 5,
      "message": "이번 선적 지연으로 새롭게 이 수출대금으로 충당 예정인 공급업체 지급일이 예상 지급기준일보다 5일 먼저 도래합니다. 다른 가용자금이나 지급일 조정 가능 여부를 확인해 주세요. 계약조건상 예상 지급기준일 기준이며, 실제 입금일은 Buyer의 송금 및 은행 처리 일정에 따라 달라질 수 있습니다."
    }
  ],
  "no_conflict_message": null,            // conflict_signals가 빈 배열일 때만 채움. 예: "🟢 현재 변경된 지급기준일(2026-10-08) 기준, 30일 이내 직접 충돌하는 금융 일정은 없습니다."

  "user_questions": [
    {
      "question_code": "Q-LOAN-01",
      "question_text": "이 수출대금이 해당 대출의 상환재원으로 계획되어 있나요?",
      "answer_status": "PENDING",          // PENDING | ANSWERED
      "answer_value": null,                // "YES" | "NO" | "UNKNOWN" (ANSWERED일 때만)
      "answered_at": null                  // ISO-8601 datetime (ANSWERED일 때만)
    }
  ],

  "source_documents": ["booking_confirmation", "commercial_invoice", "bill_of_lading"],
  "disclaimer": "본 브리핑의 예시·수치는 가상값을 포함할 수 있으며, 실제 KB 상품 이용 가능성·심사·법적 판단을 의미하지 않습니다."
}
```

### 필드 비고
- `conflict_signals`는 **`display_to_user=true`인 항목만** 포함합니다. `link_status=NOT_LINKED`(무관 확인) 또는 `NOT_EVALUATED`(미확인)인 이벤트는 이 배열에 넣지 않습니다.
- `conflict_signals`가 빈 배열이면 프론트엔드는 반드시 `no_conflict_message`를 노출해야 합니다(빈 배열 = 화면 비움이 아님).
- `same_day_flag=true`인 항목은 `display_label`과 무관하게 프론트엔드가 완충기간 0일 경고 문구를 함께 렌더링합니다(3절 참고).
- `user_questions[].answer_status="ANSWERED"`이면 프론트엔드는 입력창 대신 완료 상태 UI를 렌더링합니다(4절 참고).

---

## 3. KB 직원용 브리핑 출력 스키마 (`kb_staff_brief`)

고객용 스키마의 모든 필드를 포함하고, 아래 필드를 추가합니다.

```json
{
  "...": "customer_brief의 모든 필드 포함",

  "invoice_amount": 60000,
  "invoice_currency": "USD",
  "payment_terms_raw": "T/T 45 DAYS AFTER B/L DATE",
  "anchor_value_source": "USER_CONFIRMED",   // USER_CONFIRMED | ICC_ISBP_RULE | NOT_CONFIRMED

  "priority_view": {
    "ranking_scope": "KB_PORTFOLIO_VIEW",     // CUSTOMER_VIEW | KB_PORTFOLIO_VIEW
    "items": [
      {
        "display_owner_label": "화주 A / TXN003",   // [화주명 / 거래번호] — KB_PORTFOLIO_VIEW에서 항상 노출
        "event_id": "FE001",
        "priority_rank": 2,
        "response_priority_level": "P3",           // P1 | P2 | P3 | P4
        "impact_level": "MEDIUM",                  // CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN | REVIEW_REQUIRED
        "urgency_bucket": "D8-14",                 // 화면 배지 표시용 (정렬에는 사용 안 함)
        "days_until_event": 13,
        "link_status": "CONFIRMED",                // CONFIRMED | UNCONFIRMED
        "lead_days": 5,
        "same_day_flag": false,
        "parallel_group": null                      // 동순위 병렬 표시 시 "1-A" / "1-B" 등
      }
    ],
    "data_action_required_queue": [
      {
        "trade_id": "TXN006",
        "reason": "결제조건의 기준일(anchor)이 아직 확인되지 않아 지급기준일을 계산할 수 없습니다."
      }
    ]
  },

  "audit": [
    {
      "event_id": "FE003",
      "link_status": "NOT_LINKED",                 // NOT_LINKED(확인 후 무관) vs NOT_EVALUATED(미확인) 반드시 구분
      "dependency_scope": null,                     // FULL | PARTIAL | null
      "dependency_basis": null,                     // EVENT_AMOUNT | LOAN_BALANCE | RECEIVABLE_AMOUNT | null
      "audit_note": "참고: KB국민은행 USD/KRW 선물환 #1 일정이 지급기준일보다 8일 먼저 도래하지만, 이 수출대금과는 무관한 것으로 확인되어 낮은 우선순위로 분류됨."
    }
  ],

  "answer_history": [
    {
      "question_code": "Q-LOAN-01",
      "question_text": "이 수출대금이 해당 대출의 상환재원으로 계획되어 있나요?",
      "answer_value": "NO",
      "answered_at": "2026-10-01T14:00:00+09:00",
      "state_before": { "link_status": "UNCONFIRMED", "tier": "Tier2" },
      "state_after": { "link_status": "NOT_LINKED", "tier": "Tier1" },
      "reason": "고객 답변 'NO' — 해당 대출 상환재원으로 사용하지 않음"
    }
  ],

  "routing": [
    {
      "event_type_code": "WORKING_CAPITAL_LOAN_MATURITY",
      "is_kb_contract": true,
      "action_owner": "KB 기업금융 RM",
      "handoff_message": "KB 기업금융 담당자에게 상환재원·일정 상담을 요청해 드릴까요?"
    }
  ],

  "internal_open_items": [
    "상품 적격성 UNKNOWN — 무역금융 담당자가 한도·필요서류 확인 필요",
    "다른 가용자금·대체 입금계획 확인 여부 미확인"
  ],

  "assignment": {
    "owner_rm": null,
    "handoff_status": "NOT_ASSIGNED",
    "internal_due_date": null
  }
}
```

### 필드 비고
- `priority_view.items[].display_owner_label`은 `KB_PORTFOLIO_VIEW`에서 여러 화주·거래가 섞여 정렬될 때 항상 채워야 합니다. `CUSTOMER_VIEW`에서는 생략 가능(자기 회사 거래만 보이므로).
- `priority_view.items` 정렬 순서는 아래 6단계를 그대로 적용합니다(1~5단계가 모두 동률이면 6단계로 확정):
  1. `days_until_event` 오름차순 (정확한 일수, 구간 아님)
  2. 동률 시 `link_status`: `CONFIRMED` > `UNCONFIRMED`
  3. 동률 시 `impact_level`: `CRITICAL > HIGH > MEDIUM > LOW > UNKNOWN > REVIEW_REQUIRED`
  4. 동률 시 이벤트 유형 기본순서: `WORKING_CAPITAL_LOAN_MATURITY > FX_FORWARD_MATURITY > SUPPLIER_PAYMENT`
  5. 동률 시 `lead_days` 내림차순
  6. 그래도 동률이면 `event_id` 오름차순(결정론적 최종 타이브레이커)
- `data_action_required_queue`는 `priority_view.items`와 완전히 분리된 배열입니다. 계산중단 거래를 우선순위 리스트에 섞어 넣지 마세요.
- `impact_level` 산출은 아래 규칙을 순서대로 적용(첫 매칭 채택, 복수 매칭 시 더 높은 등급 채택):

| 순서 | 조건 | impact_level |
|---|---|---|
| Rule 1 | `link_status = UNCONFIRMED` | `UNKNOWN` |
| Rule 2 | `supplier_criticality=CRITICAL` AND `adjustability=LOW` AND `alternative_funds_status=NO` AND `disruption_risk=HIGH` | `CRITICAL` |
| Rule 3 | 이벤트 유형이 대출/선물환 AND `link_status=CONFIRMED` AND `alternative_funds_status=NO` | `HIGH` |
| Rule 4 | `link_status=CONFIRMED` AND `dependency_scope=PARTIAL` AND `alternative_funds_status=UNKNOWN` | `MEDIUM` |
| Rule 5 | `link_status=CONFIRMED` AND `alternative_funds_status=YES` AND `adjustability=HIGH` | `LOW` |
| Rule 6 (기본값) | 위 어느 것도 매칭 안 됨 | `REVIEW_REQUIRED` (자동 확정 금지, 사람 검토 필요) |

- `response_priority_level`은 오직 `days_until_event` 구간에만 의존합니다 (`impact_level=CRITICAL`이거나 `same_day_flag=true`라고 P1로 승격시키지 않음):

| days_until_event | response_priority_level |
|---|---|
| OVERDUE 또는 D-0(오늘) | P1 |
| D1-3 | P1 |
| D4-7 | P2 |
| D8-14 | P3 |
| D15+ | P4 |

---

## 4. 질문-상태전이 스펙 (Agent 대화 로직)

| question_code | trigger_condition | answer | update_field | update_value | next_tier |
|---|---|---|---|---|---|
| Q-SUPPLIER-01 | 공급업체 지급일 선행 + `link_status=UNCONFIRMED` | YES | `link_status` | `CONFIRMED` | Tier3 |
| Q-SUPPLIER-01 | 동일 | NO | `link_status` | `NOT_LINKED` | Tier1(표시 안 함) |
| Q-SUPPLIER-01 | 동일 | 모름 | `link_status` | `UNCONFIRMED` | Tier2(유지) |
| Q-LOAN-01 | 대출 만기 선행 + `link_status=UNCONFIRMED` | YES | `link_status` | `CONFIRMED` | Tier3 |
| Q-LOAN-01 | 동일 | NO | `link_status` | `NOT_LINKED` | Tier1(표시 안 함) |
| Q-LOAN-01 | 동일 | 모름 | `link_status` | `UNCONFIRMED` | Tier2(유지) |
| Q-LOAN-02 | Q-LOAN-01 답변=YES | 전액 | `dependency_scope` | `FULL` | Tier3(유지) |
| Q-LOAN-02 | 동일 | 일부 | `dependency_scope` | `PARTIAL` | Tier3(유지, 메시지에 '일부' 명시) |
| Q-FX-01 | 선물환 만기 선행 + `link_status=UNCONFIRMED` | YES | `link_status` | `CONFIRMED` | Tier3 |
| Q-FX-01 | 동일 | NO | `link_status` | `NOT_LINKED` | Tier1(표시 안 함) |
| Q-FX-01 | 동일 | 모름 | `link_status` | `UNCONFIRMED` | Tier2(유지) |

`link_status=UNASSESSED`(등록 자체가 없는 조합)는 질문을 트리거하지 않습니다. `UNCONFIRMED`(등록됐지만 미확인)만 질문 대상입니다.

답변이 접수되면 `kb_staff_brief.answer_history`에 `state_before`/`state_after`를 기록하고, 두 브리핑 모두 재계산 후 다시 렌더링합니다.

---

## 5. MVP 1차 구현 시 우선순위 로직 단순화 (선택)

개발 일정이 촉박하면 아래 2단계 정렬만 먼저 구현하고, `impact_level`·`lead_days`·`event_id` 타이브레이커는 이후 단계로 미룰 수 있습니다.

1. `days_until_event` 오름차순
2. 동률일 때만 이벤트 유형 기본순서 (`WORKING_CAPITAL_LOAN_MATURITY > FX_FORWARD_MATURITY > SUPPLIER_PAYMENT`)

단, 아래 4가지는 정렬 로직의 정교함과 무관하게 MVP 1차에도 반드시 포함합니다(UX 오해 방지 목적):
- `no_conflict_message` 명시 노출 (충돌 없음일 때 빈 배열만 두지 않기)
- `same_day_flag=true` 항목의 완충기간 경고 문구 상시 동반
- `data_action_required` 배너와 위험 충돌 리스트의 아이콘/필드 분리
- `user_questions[].answer_status="ANSWERED"` 완료 상태 UI

---

## 6. 참고: 실제 값이 아닌 항목

이 문서의 JSON 예시 값(금액, 날짜, 회사명 등)은 `화주ABC_금융일정_충돌시나리오_v8.xlsx`의 SYNTHETIC 데이터를 인용한 것으로, 실제 KB 상품·고객 데이터가 아닙니다. 필드명과 구조만 그대로 사용하시면 됩니다.
