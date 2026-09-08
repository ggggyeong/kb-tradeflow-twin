"""Small, deterministic service policies. Routing never establishes eligibility."""

from dataclasses import dataclass
from typing import Literal

from app.schemas.portfolio import (
    FinancialScenarioCode,
    PortfolioConflictResult,
    PortfolioProductOption,
    PortfolioServiceCard,
)


@dataclass(frozen=True)
class ServicePolicy:
    code: str
    title: str
    customer_need: str
    topics: tuple[str, ...]
    questions: tuple[str, ...]


POLICIES: dict[FinancialScenarioCode, ServicePolicy] = {
    "WORKING_CAPITAL_LOAN_MATURITY": ServicePolicy(
        "LOAN_REPAYMENT_REVIEW",
        "대출 상환일 점검·상담 준비",
        "입금 전에 돌아오는 상환일과 미상환 시 유의사항을 확인합니다.",
        ("LOAN_REPAYMENT", "LOAN_OVERDUE", "LOAN_EXTENSION"),
        (
            "만기일에 사용할 수 있는 잔액과 다른 입금 예정액은 얼마인가요?",
            "거래 은행에 현재 대출의 상환 방법과 연장 상담 가능 여부를 확인했나요?",
        ),
    ),
    "FX_FORWARD_MATURITY": ServicePolicy(
        "FX_SETTLEMENT_REVIEW",
        "기존 선물환 결제 의무·위험 확인",
        "외화 입금이 늦어질 때 기존 매도 계약의 결제 의무와 위험을 확인합니다.",
        ("FX_SETTLEMENT", "FX_RISK", "FX_CLOSEOUT"),
        (
            "계약서의 결제일·매도 통화·금액과 실제 확보한 외화가 일치하나요?",
            "거래 은행에 일정 변경 가능 여부와 정산 비용·손실을 확인했나요?",
        ),
    ),
    "SUPPLIER_PAYMENT": ServicePolicy(
        "SUPPLIER_FUNDING_REVIEW",
        "공급자 지급 준비·자금조달 정보 확인",
        "지급일까지 필요한 자금의 용도와 검토할 금융자료의 조건을 확인합니다.",
        (
            "FUNDING_PURPOSE",
            "FUNDING_ELIGIBILITY",
            "RECEIVABLE_TERMS",
            "RECEIVABLE_DOCUMENTS",
            "RECEIVABLE_RECOURSE",
            "RECEIVABLE_COST",
        ),
        (
            "공급자 지급액 중 보유 자금으로 충당할 수 없는 금액은 얼마인가요?",
            "지급 목적과 거래 증빙을 준비했나요? 수출채권이 있다면 양도·매입 조건도 은행에 확인하세요.",
        ),
    ),
}

TOPIC_QUERIES = {
    "LOAN_REPAYMENT": "대출 원리금 상환 방법 시기",
    "LOAN_OVERDUE": "대출 만기 원금 미상환 연체이자",
    "LOAN_EXTENSION": "대출 계약기간 연장 심사 거절 조건",
    "FX_SETTLEMENT": "선물환 매도 약정환율 만기 외화 매도 의무",
    "FX_RISK": "선물환 매도 시장환율 거래손실 위험",
    "FX_CLOSEOUT": "선물환 중도해지 은행 동의 정산 손실",
    "FUNDING_PURPOSE": "기업 운전자금 생산 판매 용도 외 유용 금지",
    "FUNDING_ELIGIBILITY": "기업대출 거래 상대방 증빙 조건",
    "RECEIVABLE_TERMS": "수출채권 매입 은행 수락 선결 조건 한도",
    "RECEIVABLE_DOCUMENTS": "수출채권 매입 선결 요건 송장 선하증권",
    "RECEIVABLE_RECOURSE": "수출채권 매입 환매 채무자 미지급",
    "RECEIVABLE_COST": "수출환어음 매출채권 할인 환가료",
}


def build_service_cards(
    conflicts: list[PortfolioConflictResult], options: list[PortfolioProductOption]
) -> list[PortfolioServiceCard]:
    cards = []
    for conflict in conflicts:
        policy = POLICIES[conflict.scenario_code]
        information = [o for o in options if o.event_id == conflict.event_id]
        status: Literal["INFORMATION", "REVIEW_REQUIRED", "NO_CONFLICT"] = (
            "NO_CONFLICT"
            if conflict.status == "NO_CONFLICT"
            else "INFORMATION"
            if conflict.status == "CONFLICT" and information
            else "REVIEW_REQUIRED"
        )
        notices = [
            "일정 차이만 확인한 결과입니다. 잔액·다른 입금·환율을 반영한 실제 부족액은 계산하지 않습니다.",
            "공개 자료는 상담 참고용이며 고객의 기존 계약이나 이용 가능 여부를 확정하지 않습니다.",
        ]
        questions = list(policy.questions)
        if conflict.status == "NO_CONFLICT":
            information, questions = [], []
            notices = ["해당 일정의 선후관계상 충돌이 없어 금융자료 검색을 생략했습니다."]
        elif conflict.status == "REVIEW_REQUIRED":
            information = []
            questions = ["대금 유입일·금융일정·거래 연결·통화와 결제 방향을 먼저 확인해 주세요."]
            notices.append("입력 확인 전에는 금융자료를 연결하지 않습니다.")
        elif not information:
            notices.append("관련 본문 근거가 부족하여 금융상품 설명을 보류했습니다.")
        cards.append(
            PortfolioServiceCard(
                event_id=conflict.event_id,
                scenario_code=conflict.scenario_code,
                service_code=policy.code,
                title=policy.title,
                customer_need=policy.customer_need,
                status=status,
                situation=conflict.reason,
                information=information,
                questions=questions,
                notices=notices,
            )
        )
    return cards
