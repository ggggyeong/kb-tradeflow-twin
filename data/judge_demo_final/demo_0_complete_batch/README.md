# Demo 0 - 8개 혼합문서 자동분류·3개 거래 매칭

한 번에 업로드하는 파일은 Booking Confirmation 3개, Commercial Invoice 3개,
Bill of Lading 2개로 총 8개입니다. 파일 순서와 이름에 의존하지 않고
seller/shipper, goods/commodity, B/L No., 항구, 선박명, 항차번호의
결정론적 점수로 세 거래를 분리합니다.

- CASE-DEMO0: Booking + Invoice + B/L (완전 거래)
- CASE-DEMO0B: Booking + Invoice + B/L (완전 거래)
- CASE-DEMO0C: Booking + Invoice (B/L 미도착 거래)

예상 결과: 8개 문서 자동분류, TradeCase 후보 3개, 완전 거래 2개,
B/L 대기 거래 1개. B/L 전체 문서의 미도착은 수기 필드 질문으로 바꾸지 않고
AWAITING_DOCUMENT 상태로 저장합니다.
