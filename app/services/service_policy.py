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
class SearchQuestion:
    """Information need, not a predetermined source/page/topic answer."""

    query_id: str
    text: str
    receivables_only: bool = False


@dataclass(frozen=True)
class AnswerSlot:
    """Question to answer from retrieved evidence, never a fixed answer or page."""

    slot_id: str
    title: str
    query_ids: tuple[str, ...]


@dataclass(frozen=True)
class ServicePolicy:
    code: str
    title: str
    customer_need: str
    search_questions: tuple[SearchQuestion, ...]
    questions: tuple[str, ...]


POLICIES: dict[FinancialScenarioCode, ServicePolicy] = {
    "WORKING_CAPITAL_LOAN_MATURITY": ServicePolicy(
        "LOAN_REPAYMENT_REVIEW",
        "대출 상환일 점검·상담 준비",
        "입금 전에 돌아오는 상환일과 미상환 시 유의사항을 확인합니다.",
        (
            SearchQuestion(
                "loan_repayment_risk",
                "기업대출 만기에 원금을 상환하지 못할 때 연체이자와 불이익은 무엇인가?",
            ),
            SearchQuestion(
                "loan_extension_preparation",
                "기업대출 계약기간을 연장할 때 적용되는 조건은 무엇인가?",
            ),
            SearchQuestion(
                "loan_renewal_documents",
                "기업대출 유지·갱신을 위해 은행에 제출해야 하는 재무제표와 매출 관련 서류는 무엇인가?",
            ),
        ),
        (
            "만기일에 사용할 수 있는 잔액과 다른 입금 예정액은 얼마인가요?",
            "거래 은행에 현재 대출의 상환 방법과 연장 상담 가능 여부를 확인했나요?",
        ),
    ),
    "FX_FORWARD_MATURITY": ServicePolicy(
        "FX_SETTLEMENT_REVIEW",
        "기존 선물환 결제 의무·위험 확인",
        "외화 입금이 늦어질 때 기존 매도 계약의 결제 의무와 위험을 확인합니다.",
        (
            SearchQuestion(
                "fx_settlement_obligation",
                "선물환 고객매도 계약에서 약정환율과 계약금액을 정하고 만기에 결제하는 방식은 무엇인가?",
            ),
            SearchQuestion(
                "fx_closeout_risk",
                "선물환 매도 계약의 중도해지에 필요한 은행 동의와 정산금액 및 손실 위험은 무엇인가?",
            ),
        ),
        (
            "계약서의 결제일·매도 통화·금액과 실제 확보한 외화가 일치하나요?",
            "거래 은행에 일정 변경 가능 여부와 정산 비용·손실을 확인했나요?",
        ),
    ),
    "SUPPLIER_PAYMENT": ServicePolicy(
        "SUPPLIER_FUNDING_REVIEW",
        "공급자 지급 준비·자금조달 정보 확인",
        "공급자 지급자금을 먼저 확인하고, 추가 자금이 필요한 경우의 조건·비용·준비자료를 검토합니다.",
        (
            SearchQuestion(
                "funding_conditions",
                "공급자 대금 지급을 위해 기업 운전자금을 검토할 때 자금용도 제한과 거래 대상 및 증빙 자료는 무엇인가?",
            ),
            SearchQuestion(
                "funding_costs",
                "기업대출 운영자금을 잠시 사용하고 상환할 때 확인해야 하는 이자와 수수료 및 중도상환 비용은 무엇인가?",
            ),
            SearchQuestion(
                "receivable_purchase_conditions",
                "수출채권 매입 요청을 은행이 수락하는 조건과 매입 한도는 무엇인가?",
                True,
            ),
            SearchQuestion(
                "receivable_required_documents",
                "수출채권 매입의 선결 요건으로 고객이 은행에 제출해야 하는 서류와 선적서류는 무엇인가?",
                True,
            ),
            SearchQuestion(
                "receivable_fees",
                "수출채권 매입의 할인수수료와 조정수수료는 어떻게 정해지며 어떤 경우에 적용되는가?",
                True,
            ),
            SearchQuestion(
                "receivable_repurchase_obligation",
                "수출채권을 매입한 후 채무자가 대금을 지급하지 않으면 고객은 언제 채권을 환매해야 하는가?",
                True,
            ),
        ),
        (
            "공급자 지급액 중 보유 자금으로 충당할 수 없는 금액은 얼마인가요?",
            "지급 목적과 거래 증빙을 준비했나요? 수출채권이 있다면 양도·매입 조건도 은행에 확인하세요.",
        ),
    ),
}


def get_answer_slots(
    scenario_code: FinancialScenarioCode, *, export_receivable_confirmed: bool
) -> tuple[AnswerSlot, ...]:
    """Keep the short customer explanation focused on distinct information needs."""
    if scenario_code == "WORKING_CAPITAL_LOAN_MATURITY":
        return (
            AnswerSlot(
                "unpaid_principal",
                "원금을 약정일에 상환하지 못하면 어떤 불이익이 있나요?",
                ("loan_repayment_risk",),
            ),
            AnswerSlot(
                "extension_conditions",
                "대출 만기 연장이 거절될 수 있는 조건은 무엇인가요?",
                ("loan_extension_preparation",),
            ),
            AnswerSlot(
                "renewal_documents",
                "대출 유지·갱신을 위해 어떤 자료를 준비해야 하나요?",
                ("loan_renewal_documents",),
            ),
        )
    if scenario_code == "FX_FORWARD_MATURITY":
        return (
            AnswerSlot(
                "settlement_obligation",
                "기존 선물환 매도 계약의 만기 결제 의무는 무엇인가요?",
                ("fx_settlement_obligation",),
            ),
            AnswerSlot(
                "closeout_conditions",
                "중도해지에는 은행의 동의가 필요한가요? 어떤 점을 확인해야 하나요?",
                ("fx_closeout_risk",),
            ),
        )
    funding = AnswerSlot(
        "funding_conditions",
        "추가 운전자금을 검토할 때 자금 사용 용도에서 주의할 점은 무엇인가요?",
        ("funding_conditions",),
    )
    if export_receivable_confirmed:
        return (
            funding,
            AnswerSlot(
                "receivable_requirements",
                "수출채권 매입 요청의 조건이나 준비해야 할 서류는 무엇인가요?",
                ("receivable_purchase_conditions", "receivable_required_documents"),
            ),
            AnswerSlot(
                "receivable_repurchase",
                "채무자가 대금을 지급하지 않은 경우, 언제 누구의 요청으로 수출채권을 환매해야 하나요?",
                ("receivable_repurchase_obligation",),
            ),
        )
    return (
        funding,
        AnswerSlot(
            "funding_costs",
            "추가 운영자금을 이용한 뒤 상환할 때 어떤 비용을 확인해야 하나요?",
            ("funding_costs",),
        ),
    )


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
            notices.append(
                "제공 가능한 금융 설명이 없습니다. 검색·생성·근거 대조 단계의 확인사항을 살펴봐 주세요."
            )
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
