from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from app.domain_inputs.providers import financial_priority_policy
from app.schemas.financial_calendar import (
    FinancialPriorityFact,
    FinancialPriorityPolicy,
    FinancialPriorityResult,
    FinancialRiskComparison,
    FinancialRiskEvaluation,
    FinancialRiskEvaluationInput,
    FinancialRiskEventFact,
)

DISPLAY_LINK_STATUSES = {"CONFIRMED", "UNCONFIRMED"}


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DeterministicFinancialRiskEngine:
    """Workbook-backed due-date conflict and response-priority engine."""

    def __init__(self, policy: FinancialPriorityPolicy | None = None) -> None:
        self.policy = policy or financial_priority_policy()

    def response_priority(self, days_until_event: int) -> str:
        matches = [
            band.priority for band in self.policy.response_bands if band.includes(days_until_event)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Priority workbook bands must match exactly once for "
                f"days_until_event={days_until_event}, got {matches}"
            )
        return matches[0]

    def urgency_bucket(self, days_until_event: int) -> str:
        matches = [
            band.label for band in self.policy.urgency_bands if band.includes(days_until_event)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Urgency workbook bands must match exactly once for "
                f"days_until_event={days_until_event}, got {matches}"
            )
        return matches[0]

    def impact_level(self, event: FinancialRiskEventFact) -> str:
        """Use only link facts that the two-sheet contract can actually remediate."""
        if event.link_status == "UNCONFIRMED":
            return "UNKNOWN"
        if event.link_status == "CONFIRMED" and event.dependency_scope == "PARTIAL":
            return "MEDIUM"
        return "REVIEW_REQUIRED"

    @staticmethod
    def _link_data_actions(
        evaluation_input: FinancialRiskEvaluationInput,
        conflicts: list[FinancialRiskComparison],
    ) -> list[dict[str, Any]]:
        """Request only optional link details needed by an actual conflict.

        The remediation is intentionally staged: first establish whether the
        transaction depends on the whole event or only part of it.  Amount and
        currency are required only for a reviewed PARTIAL dependency.
        """
        facts_by_event = {event.financial_event_id: event for event in evaluation_input.events}
        actions: list[dict[str, Any]] = []
        for conflict in conflicts:
            event = facts_by_event[conflict.financial_event_id]
            if event.transaction_event_link_id is None:
                continue
            scope = (event.dependency_scope or "UNKNOWN").strip().upper()
            if scope not in {"FULL", "PARTIAL"}:
                missing_fields = ["dependency_scope"]
            elif scope == "PARTIAL":
                missing_fields = []
                if event.linked_amount is None:
                    missing_fields.append("linked_amount")
                if not (event.linked_currency or "").strip():
                    missing_fields.append("linked_currency")
            else:
                missing_fields = []
            if not missing_fields:
                continue
            actions.append(
                {
                    "code": "FINANCIAL_LINK_DETAILS_REQUIRED",
                    "case_id": evaluation_input.case_id,
                    "company_id": evaluation_input.company_id,
                    "transaction_id": evaluation_input.transaction_id,
                    "transaction_event_link_id": event.transaction_event_link_id,
                    "financial_event_id": event.financial_event_id,
                    "missing_fields": missing_fields,
                    "message": (
                        "충돌 노출 범위를 확정하려면 거래-금융이벤트 연결의 "
                        f"{', '.join(missing_fields)} 값을 확인해 주세요."
                    ),
                }
            )
        return actions

    @staticmethod
    def _event_receipt_reference(
        evaluation_input: FinancialRiskEvaluationInput,
        event: FinancialRiskEventFact,
    ) -> tuple[float | None, str | None]:
        """Resolve exposure amount from staged link details without guessing."""
        scope = (event.dependency_scope or "UNKNOWN").strip().upper()
        if scope == "FULL":
            return event.amount, event.currency
        if scope == "PARTIAL":
            return event.linked_amount, event.linked_currency
        return (
            evaluation_input.expected_receipt_amount,
            evaluation_input.expected_receipt_currency,
        )

    def _event_type_order(self, event_type_code: str) -> int:
        compatibility = {
            "LOAN_MATURITY": "WORKING_CAPITAL_LOAN_MATURITY",
            "FORWARD_MATURITY": "FX_FORWARD_MATURITY",
            "PAYABLE": "SUPPLIER_PAYMENT",
        }
        canonical = compatibility.get(event_type_code, event_type_code)
        return self.policy.event_type_order.get(canonical, 99)

    def _routing(self, event_type_code: str, is_kb_contract: bool | None) -> dict[str, str] | None:
        compatibility = {
            "LOAN_MATURITY": "WORKING_CAPITAL_LOAN_MATURITY",
            "FORWARD_MATURITY": "FX_FORWARD_MATURITY",
            "PAYABLE": "SUPPLIER_PAYMENT",
        }
        canonical = compatibility.get(event_type_code, event_type_code)
        suffix = "ANY" if is_kb_contract is None else "TRUE" if is_kb_contract else "FALSE"
        return self.policy.routing.get(
            f"{canonical}:{suffix}",
            self.policy.routing.get(f"{canonical}:ANY"),
        )

    @staticmethod
    def display_label(link_status: str, same_day_flag: bool) -> str:
        if link_status == "CONFIRMED":
            label = "확정된 충돌"
        elif link_status == "UNCONFIRMED":
            label = "긴급 확인 필요"
        else:
            label = "표시 대상 아님"
        return f"{label} + ★동일일" if same_day_flag else label

    def _priority_sort_key(
        self,
        *,
        days_until_event: int,
        link_status: str,
        impact_level: str,
        event_type_code: str,
        lead_days: int,
        event_id: str,
    ) -> tuple[int, int, int, int, int, str]:
        return (
            days_until_event,
            self.policy.link_status_order.get(link_status, 99),
            self.policy.impact_level_order.get(impact_level, 99),
            self._event_type_order(event_type_code),
            -lead_days,
            event_id,
        )

    def rank_priority_facts(
        self,
        facts: list[FinancialPriorityFact],
        *,
        ranking_scope: str,
        company_id: str | None = None,
    ) -> list[FinancialPriorityResult]:
        """Rank reviewed priority facts within an explicit display scope."""
        if ranking_scope == "CUSTOMER_VIEW":
            if not company_id:
                raise ValueError("CUSTOMER_VIEW requires company_id isolation")
            selected = [fact for fact in facts if fact.company_id == company_id]
        elif ranking_scope == "KB_PORTFOLIO_VIEW":
            selected = list(facts)
        else:
            raise ValueError(f"Unsupported ranking_scope: {ranking_scope}")
        selected.sort(
            key=lambda fact: self._priority_sort_key(
                days_until_event=fact.days_until_event,
                link_status=fact.link_status,
                impact_level=fact.impact_level,
                event_type_code=fact.event_type_code,
                lead_days=fact.lead_days,
                event_id=fact.event_id,
            )
        )
        ranked: list[FinancialPriorityResult] = []
        for rank, fact in enumerate(selected, start=1):
            route = self._routing(fact.event_type_code, fact.is_kb_contract)
            ranked.append(
                FinancialPriorityResult(
                    **fact.model_dump(),
                    response_priority_level=self.response_priority(fact.days_until_event),
                    priority_rank=rank,
                    urgency_bucket=self.urgency_bucket(fact.days_until_event),
                    display_label=self.display_label(fact.link_status, fact.same_day_flag),
                    action_owner=route["action_owner"] if route else None,
                )
            )
        return ranked

    @staticmethod
    def _tier(link_status: str, lead_days: int) -> tuple[str, bool]:
        if lead_days < 0:
            return "정상", False
        if link_status == "NOT_LINKED":
            return "Tier1(명시적 무관)", False
        if link_status == "UNASSESSED":
            return "NOT_EVALUATED", False
        if lead_days == 0 and link_status in DISPLAY_LINK_STATUSES:
            return "SAME_DAY_REVIEW", True
        if link_status == "CONFIRMED":
            return "Tier3", True
        if link_status == "UNCONFIRMED":
            return "Tier2", True
        return "NOT_EVALUATED", False

    @staticmethod
    def _conflict_origin(planned_lead_days: int | None, revised_lead_days: int) -> str:
        if revised_lead_days < 0:
            return "NO_CONFLICT"
        if revised_lead_days == 0:
            return "SAME_DAY_REVIEW"
        if planned_lead_days is not None and planned_lead_days <= 0:
            return "NEW_CONFLICT_BY_DELAY"
        return "PRE_EXISTING_WORSENED"

    def _evaluate_unresolved_anchor(
        self,
        evaluation_input: FinancialRiskEvaluationInput,
        input_fingerprint: str,
        expected_receipt: dict[str, Any],
    ) -> FinancialRiskEvaluation:
        if evaluation_input.tenor_days is None:
            return FinancialRiskEvaluation(
                case_id=evaluation_input.case_id,
                company_id=evaluation_input.company_id,
                transaction_id=evaluation_input.transaction_id,
                as_of_date=evaluation_input.as_of_date,
                source_kind=evaluation_input.source_kind,
                source_ref_id=evaluation_input.source_ref_id,
                status="DATA_ACTION_REQUIRED",
                expected_receipt=expected_receipt,
                data_actions=[
                    {
                        "code": "PAYMENT_DUE_DATE_REQUIRED",
                        "transaction_id": evaluation_input.transaction_id,
                        "message": (
                            "결제조건의 기준일과 기한이 확인되지 않아 "
                            "금융 일정 충돌을 계산하지 않았습니다."
                        ),
                    }
                ],
                priority_rule_version=self.policy.version,
                input_fingerprint=input_fingerprint,
            )

        comparisons: list[FinancialRiskComparison] = []
        for event in evaluation_input.events:
            latest_safe_anchor = event.event_date - timedelta(days=evaluation_input.tenor_days)
            frontier_days_remaining = (latest_safe_anchor - evaluation_input.as_of_date).days
            days_until_event = (event.event_date - evaluation_input.as_of_date).days
            relevant = event.link_status in DISPLAY_LINK_STATUSES
            frontier_triggered = (
                relevant and frontier_days_remaining <= evaluation_input.frontier_warning_days
            )
            tier = (
                "Tier3"
                if event.link_status == "CONFIRMED"
                else "Tier2"
                if event.link_status == "UNCONFIRMED"
                else "Tier1"
                if event.link_status == "NOT_LINKED"
                else "NOT_EVALUATED"
            )
            impact_level = self.impact_level(event)
            response_priority = self.response_priority(days_until_event)
            route = self._routing(event.event_type_code, event.is_kb_contract)
            receipt_amount, receipt_currency = self._event_receipt_reference(
                evaluation_input, event
            )
            comparison_id = f"CMP-{
                _stable_hash(
                    {
                        'case_id': evaluation_input.case_id,
                        'source_ref_id': evaluation_input.source_ref_id,
                        'event_id': event.financial_event_id,
                        'latest_safe_anchor': latest_safe_anchor.isoformat(),
                    }
                )[:20]
            }"
            reason = (
                f"{event.event_name}: shipping anchor unresolved; "
                f"latest_safe_anchor={latest_safe_anchor.isoformat()}, "
                f"frontier_days_remaining={frontier_days_remaining}, "
                f"event={event.event_date.isoformat()}, "
                f"link_status={event.link_status}"
            )
            comparisons.append(
                FinancialRiskComparison(
                    comparison_id=comparison_id,
                    case_id=evaluation_input.case_id,
                    company_id=evaluation_input.company_id,
                    transaction_id=evaluation_input.transaction_id,
                    financial_event_id=event.financial_event_id,
                    transaction_event_link_id=event.transaction_event_link_id,
                    event_type=event.event_type_code,
                    event_name=event.event_name,
                    event_date=event.event_date,
                    amount=event.amount,
                    currency=event.currency,
                    direction="OUTFLOW",
                    link_type=event.link_type,
                    link_status=event.link_status,
                    planned_due_date=evaluation_input.planned_due_date,
                    revised_due_date=None,
                    expected_payment_date=None,
                    latest_safe_anchor_date=latest_safe_anchor,
                    frontier_days_remaining=frontier_days_remaining,
                    expected_receipt_amount=receipt_amount,
                    expected_receipt_currency=receipt_currency,
                    planned_lead_days=None,
                    lead_days=0,
                    gap_days=0,
                    days_until_event=days_until_event,
                    urgency_bucket=self.urgency_bucket(days_until_event),
                    date_relation="ANCHOR_UNRESOLVED",
                    conflict_origin=(
                        "PREEMPTIVE_BREACH" if frontier_triggered else "FRONTIER_NOT_REACHED"
                    ),
                    tier=tier,
                    display_to_user=frontier_triggered,
                    conflict=frontier_triggered,
                    same_day_flag=False,
                    impact_level=impact_level,
                    response_priority_level=response_priority,
                    priority=response_priority,
                    display_label=(
                        self.display_label(event.link_status, False) if frontier_triggered else None
                    ),
                    action_owner=route["action_owner"] if route else None,
                    priority_basis={
                        "rule_version": self.policy.version,
                        "source_sha256": self.policy.source_sha256,
                        "mode": "UNRESOLVED_ANCHOR_FRONTIER",
                        "tenor_days": evaluation_input.tenor_days,
                        "latest_safe_anchor_date": (latest_safe_anchor.isoformat()),
                        "frontier_warning_days": (evaluation_input.frontier_warning_days),
                        "days_until_event": days_until_event,
                    },
                    reason=reason,
                )
            )

        conflicts = [item for item in comparisons if item.display_to_user]
        conflicts.sort(
            key=lambda item: self._priority_sort_key(
                days_until_event=item.days_until_event,
                link_status=item.link_status,
                impact_level=item.impact_level,
                event_type_code=item.event_type,
                lead_days=item.lead_days,
                event_id=item.financial_event_id,
            )
        )
        ranked_conflicts = [
            item.model_copy(update={"priority_rank": rank})
            for rank, item in enumerate(conflicts, start=1)
        ]
        ranked_by_id = {item.comparison_id: item for item in ranked_conflicts}
        data_actions = self._link_data_actions(evaluation_input, ranked_conflicts)
        return FinancialRiskEvaluation(
            case_id=evaluation_input.case_id,
            company_id=evaluation_input.company_id,
            transaction_id=evaluation_input.transaction_id,
            as_of_date=evaluation_input.as_of_date,
            source_kind=evaluation_input.source_kind,
            source_ref_id=evaluation_input.source_ref_id,
            status="EVALUATED",
            expected_receipt=expected_receipt,
            highest_priority=(
                ranked_conflicts[0].response_priority_level if ranked_conflicts else None
            ),
            comparisons=[ranked_by_id.get(item.comparison_id, item) for item in comparisons],
            conflicts=ranked_conflicts,
            data_actions=data_actions,
            priority_rule_version=self.policy.version,
            input_fingerprint=input_fingerprint,
        )

    def evaluate(self, evaluation_input: FinancialRiskEvaluationInput) -> FinancialRiskEvaluation:
        """Evaluate all same-company events, retaining excluded rows for audit."""
        normalized_input = evaluation_input.model_copy(
            update={
                "events": sorted(
                    evaluation_input.events,
                    key=lambda event: event.financial_event_id,
                )
            }
        )
        fingerprint_payload = normalized_input.model_dump(mode="json")
        fingerprint_payload["priority_source_sha256"] = self.policy.source_sha256
        input_fingerprint = _stable_hash(fingerprint_payload)
        expected_receipt = {
            "date": (
                normalized_input.revised_due_date.isoformat()
                if normalized_input.revised_due_date
                else None
            ),
            "amount": normalized_input.expected_receipt_amount,
            "currency": normalized_input.expected_receipt_currency,
        }
        if normalized_input.revised_due_date is None:
            return self._evaluate_unresolved_anchor(
                normalized_input, input_fingerprint, expected_receipt
            )

        comparisons: list[FinancialRiskComparison] = []
        for event in normalized_input.events:
            planned_lead_days = (
                (normalized_input.planned_due_date - event.event_date).days
                if normalized_input.planned_due_date
                else None
            )
            lead_days = (normalized_input.revised_due_date - event.event_date).days
            gap_days = -lead_days
            days_until_event = (event.event_date - normalized_input.as_of_date).days
            date_relation = (
                "EVENT_FIRST"
                if lead_days > 0
                else "SAME_DAY"
                if lead_days == 0
                else "DUE_DATE_FIRST"
            )
            tier, display_to_user = self._tier(event.link_status, lead_days)
            impact_level = self.impact_level(event)
            response_priority = self.response_priority(days_until_event)
            same_day_flag = lead_days == 0
            route = self._routing(event.event_type_code, event.is_kb_contract)
            receipt_amount, receipt_currency = self._event_receipt_reference(
                normalized_input, event
            )
            same_currency = bool(receipt_currency and event.currency == receipt_currency)
            uncovered_amount = (
                max(
                    event.amount - (receipt_amount or 0.0),
                    0.0,
                )
                if same_currency and receipt_amount is not None
                else None
            )
            comparison_id = f"CMP-{
                _stable_hash(
                    {
                        'case_id': normalized_input.case_id,
                        'source_ref_id': normalized_input.source_ref_id,
                        'event_id': event.financial_event_id,
                        'revised_due_date': normalized_input.revised_due_date.isoformat(),
                    }
                )[:20]
            }"
            conflict_origin = self._conflict_origin(planned_lead_days, lead_days)
            reason = (
                f"{event.event_name}: event={event.event_date.isoformat()}, "
                f"due={normalized_input.revised_due_date.isoformat()}, "
                f"lead_days={lead_days}, link_status={event.link_status}, "
                f"tier={tier}"
            )
            comparisons.append(
                FinancialRiskComparison(
                    comparison_id=comparison_id,
                    case_id=normalized_input.case_id,
                    company_id=normalized_input.company_id,
                    transaction_id=normalized_input.transaction_id,
                    financial_event_id=event.financial_event_id,
                    transaction_event_link_id=event.transaction_event_link_id,
                    event_type=event.event_type_code,
                    event_name=event.event_name,
                    event_date=event.event_date,
                    amount=event.amount,
                    currency=event.currency,
                    direction="OUTFLOW",
                    link_type=event.link_type,
                    link_status=event.link_status,
                    planned_due_date=normalized_input.planned_due_date,
                    revised_due_date=normalized_input.revised_due_date,
                    expected_payment_date=normalized_input.revised_due_date,
                    expected_receipt_amount=receipt_amount,
                    expected_receipt_currency=receipt_currency,
                    planned_lead_days=planned_lead_days,
                    lead_days=lead_days,
                    gap_days=gap_days,
                    days_until_event=days_until_event,
                    urgency_bucket=self.urgency_bucket(days_until_event),
                    date_relation=date_relation,
                    conflict_origin=conflict_origin,
                    tier=tier,
                    display_to_user=display_to_user,
                    conflict=display_to_user,
                    same_day_flag=same_day_flag,
                    impact_level=impact_level,
                    response_priority_level=response_priority,
                    priority=response_priority,
                    display_label=(
                        self.display_label(event.link_status, same_day_flag)
                        if display_to_user
                        else None
                    ),
                    action_owner=route["action_owner"] if route else None,
                    uncovered_amount=uncovered_amount,
                    priority_basis={
                        "rule_version": self.policy.version,
                        "source_sha256": self.policy.source_sha256,
                        "sort_keys": [
                            "days_until_event ASC",
                            "link_status CONFIRMED>UNCONFIRMED",
                            "impact_level",
                            "event_type",
                            "lead_days DESC",
                            "event_id ASC",
                        ],
                        "days_until_event": days_until_event,
                        "response_priority_level": response_priority,
                        "impact_level": impact_level,
                    },
                    reason=reason,
                )
            )

        conflicts = [item for item in comparisons if item.display_to_user]
        conflicts.sort(
            key=lambda item: self._priority_sort_key(
                days_until_event=item.days_until_event,
                link_status=item.link_status,
                impact_level=item.impact_level,
                event_type_code=item.event_type,
                lead_days=item.lead_days,
                event_id=item.financial_event_id,
            )
        )
        ranked_conflicts = [
            item.model_copy(update={"priority_rank": rank})
            for rank, item in enumerate(conflicts, start=1)
        ]
        ranked_by_id = {item.comparison_id: item for item in ranked_conflicts}
        audited_comparisons = [ranked_by_id.get(item.comparison_id, item) for item in comparisons]
        data_actions = self._link_data_actions(normalized_input, ranked_conflicts)
        return FinancialRiskEvaluation(
            case_id=normalized_input.case_id,
            company_id=normalized_input.company_id,
            transaction_id=normalized_input.transaction_id,
            as_of_date=normalized_input.as_of_date,
            source_kind=normalized_input.source_kind,
            source_ref_id=normalized_input.source_ref_id,
            status="EVALUATED",
            expected_receipt=expected_receipt,
            highest_priority=(
                ranked_conflicts[0].response_priority_level if ranked_conflicts else None
            ),
            comparisons=audited_comparisons,
            conflicts=ranked_conflicts,
            data_actions=data_actions,
            priority_rule_version=self.policy.version,
            input_fingerprint=input_fingerprint,
        )
