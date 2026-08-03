# Demo 1 - 금일 금융위험 분석

1. Booking과 Invoice를 먼저 등록합니다. B/L 전체 문서는 아직 없으므로
   수기 필드 입력을 요구하지 않고 AWAITING_BILL_OF_LADING 상태로 보존합니다.
2. financial_calendar_seed.json을 그대로 2-Sheet XLSX로 작성해 등록합니다.
3. 시스템 날짜를 2026-08-20으로 두고 챗봇 메뉴 2번을 실행합니다.
4. 위험 요약 뒤 상품 검토와 고객용·RM용 보고서 생성 동의를 선택합니다.

예상 핵심: ETD 2026-08-05, 대출만기 2026-10-10, 60일 결제조건,
기준 선적일 2026-08-11 경과로 PREEMPTIVE_BREACH/ACTION_REQUIRED.
