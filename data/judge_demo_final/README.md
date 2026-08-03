# KB TradeFlow Twin 최종 Demo 0·1·2 자료

이 폴더는 심사위원 시연용 합성 자료만 모아 둔 최종 포트폴리오입니다. 서로 다른
회사·거래·batch ID를 사용하므로 세 데모의 DB 결과가 섞이지 않습니다.

## Demo 0 — 완전한 3문서 분류·거래 매칭

- 폴더: `demo_0_complete_batch/`
- 입력: Booking 1건, Commercial Invoice 1건, B/L 1건
- 기대: 3종 자동 분류 → 결정론적 점수 매칭 → `CASE-DEMO0` 1건 commit
- Core22 누락 및 Human 질문 없음

## Demo 1 — B/L 미수령 거래의 금일 선제 위험 분석

- 폴더: `demo_1_manual_today_risk/`
- 사전 입력: Booking 1건, Invoice 1건, 금융일정 seed
- B/L 전체 문서는 아직 없으며 `AWAITING_DOCUMENT` 상태로 계속 관리
- 기준일: 2026-08-20
- 기대: `CASE-DEMO1`의 안전 On-board 기준일 2026-08-11 경과를 선제 탐지
- 후속: 상품 원문 근거 → 고객용·RM용 PDF → 상담사 연결 UI

## Demo 2 — 사용자 제보 9일 지연 분석

- 폴더: `demo_2_reported_delay/`
- 사전 입력: Booking 1건, Invoice 1건, 금융일정 seed
- 사용자 문장: `CASE-DEMO2 거래의 선적이 9일 늦어질 예정입니다`
- 기대: 2026-09-29 계획 회수일이 2026-10-08로 이동하여 2026-10-03
  공급업체 지급과 5일 충돌
- Human: On-board date 적용 → 의존범위 `전액(FULL)`
- 후속: 상품 원문 근거 → 고객용·RM용 PDF → 상담사 연결 UI

## 생성·준비

```bash
python scripts/generate_final_demo_portfolio.py
python scripts/prepare_final_demo_portfolio.py --demo all
```

두 스크립트 모두 OpenAI/LangSmith API를 호출하지 않습니다. 금융일정은
`financial_calendar_seed.json`의 2-Sheet 최소 계약을 준비 스크립트가 동일한 도메인
계약으로 정규화해 DB에 반영합니다. 사용자 조사 원본은 `reference/`에 보존합니다.

