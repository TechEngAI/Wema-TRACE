"""Deterministic Transaction Outcome Rules Engine (Part 4).

Consumes canonical normalized events (Part 2) and their deterministic
correlation groups (Part 3) and emits a single, fully justified outcome under
the frozen five-outcome / four-strength contract.

CRITICAL INVARIANTS:
- Deterministic: identical inputs produce byte-for-byte identical output.  Every
  set-derived collection is sorted so Python hash randomization cannot leak in.
- Pure: never mutates events, raw records, or database state; never persists.
- Honest: amount/time proximity never confirms a link; only Part 3 CONFIRMED
  groups may establish linkage-dependent findings.  Contradictions are surfaced.
- Bounded: does NOT invent evidence, probabilities, or percentages.  Unknown
  situations degrade to INSUFFICIENT_EVIDENCE rather than guessing.
- Traceable: every finding enumerates supporting and contradicting evidence IDs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from uuid import UUID

from app.config import settings
from app.correlation.engine import correlate_events
from app.db.models import CaseModel, EventModel
from app.schemas.analysis import (
    AnalysisResponse,
    CounterfactualItem,
    DataQualityFlagItem,
    MissingEvidenceItem,
    RecommendedEvidenceItem,
    TimelineEvent,
)
from app.schemas.correlation import (
    CaseCorrelationResult,
    CorrelationGroup,
    CorrelationReasonCode,
    CorrelationStatusEnum,
)
from app.schemas.enums import (
    EventActionEnum,
    EventStatusEnum,
    FlagSeverityEnum,
    InstitutionPartyEnum,
    OutcomeEnum,
    StrengthEnum,
)

RULE_VERSION = "2026.1.0-rules"

#: Logical party progression used only to reason about temporal ordering.
_PARTY_RANK: Dict[str, int] = {
    InstitutionPartyEnum.ORIGINATING_BANK.value: 0,
    InstitutionPartyEnum.SWITCH.value: 1,
    InstitutionPartyEnum.RECEIVING_BANK.value: 2,
    InstitutionPartyEnum.MERCHANT.value: 2,
}

#: Core money-movement actions that participate in linkage-dependent rules.
_CORE_ACTIONS: Set[EventActionEnum] = {
    EventActionEnum.DEBIT,
    EventActionEnum.CREDIT,
    EventActionEnum.SWITCH_IN,
    EventActionEnum.SWITCH_OUT,
}

#: Payment stage (leg) each action belongs to.  A single transaction progresses
#: through distinct legs; only claims about the *same* leg can be genuinely
#: contradictory.  A SUCCESS on one leg and a FAILED on a different leg is a
#: normal cross-stage sequence, not a contradiction.  ``SWITCH_IN`` and
#: ``CREDIT`` both describe the inbound/settlement leg (a switch's claim that
#: the receiving bank accepted versus the receiving bank's own core record).
_LEG_BY_ACTION: Dict[EventActionEnum, str] = {
    EventActionEnum.DEBIT: "ORIGINATION",
    EventActionEnum.SWITCH_OUT: "OUTBOUND_TRANSIT",
    EventActionEnum.SWITCH_IN: "INBOUND_SETTLEMENT",
    EventActionEnum.CREDIT: "INBOUND_SETTLEMENT",
}

_STRENGTH_ORDER: Dict[StrengthEnum, int] = {
    StrengthEnum.INSUFFICIENT: 0,
    StrengthEnum.LOW: 1,
    StrengthEnum.MODERATE: 2,
    StrengthEnum.HIGH: 3,
}

#: Severities that cap an otherwise-strong finding at MODERATE.
_CAPPING_SEVERITIES: Set[FlagSeverityEnum] = {
    FlagSeverityEnum.CRITICAL,
    FlagSeverityEnum.MEDIUM,
}


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalize an identifier: strip whitespace, collapse blanks to None."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _party(event: EventModel) -> InstitutionPartyEnum:
    return InstitutionPartyEnum(event.party)


def _action(event: EventModel) -> EventActionEnum:
    return EventActionEnum(event.action)


def _status(event: EventModel) -> EventStatusEnum:
    return EventStatusEnum(event.status)


def _event_sort_key(event: EventModel) -> Tuple[datetime, str, str]:
    """Deterministic canonical event ordering: timestamp, party, event_id."""
    return (event.event_timestamp, event.party, str(event.id))


def _sorted_ids(events: Sequence[EventModel]) -> List[UUID]:
    return sorted({e.id for e in events}, key=str)


def _signature(event: EventModel) -> Tuple[Any, ...]:
    """Full evidence fingerprint used to detect probable duplicate records."""
    return (
        event.party,
        event.action,
        event.status,
        event.amount_minor_units,
        event.currency,
        _clean(event.session_id),
        _clean(event.rrn),
        _clean(event.reference),
        event.event_timestamp.isoformat(),
    )


def _deduplicate(
    events: Sequence[EventModel],
) -> Tuple[List[EventModel], List[EventModel]]:
    """Split events into canonical (distinct) legs and exact duplicates.

    A duplicate never manufactures a second payment: exactly one canonical leg
    per identical signature survives (lowest event ID, deterministic).
    """
    buckets: Dict[Tuple[Any, ...], List[EventModel]] = {}
    for event in sorted(events, key=_event_sort_key):
        buckets.setdefault(_signature(event), []).append(event)

    canonical: List[EventModel] = []
    duplicates: List[EventModel] = []
    for _sig in sorted(buckets, key=lambda s: tuple(str(part) for part in s)):
        members = sorted(buckets[_sig], key=lambda e: str(e.id))
        canonical.append(members[0])
        duplicates.extend(members[1:])
    return canonical, duplicates


def _events_by_id(events: Sequence[EventModel]) -> Dict[UUID, EventModel]:
    return {e.id: e for e in events}


def _build_timeline(events: Sequence[EventModel]) -> List[TimelineEvent]:
    """Correlation-verified canonical timeline, deterministically ordered."""
    return [
        TimelineEvent(
            event_id=e.id,
            timestamp=e.event_timestamp,
            party=_party(e),
            action=_action(e),
            status=_status(e),
            amount_minor_units=e.amount_minor_units,
            currency=e.currency,
            reference=e.reference,
            session_id=e.session_id,
            rrn=e.rrn,
            channel=e.channel,
            raw_record_id=e.raw_record_id,
            data_quality_notes=None,
        )
        for e in sorted(events, key=_event_sort_key)
    ]


def _evidence_window(
    events: Sequence[EventModel], case: CaseModel
) -> Tuple[datetime, datetime]:
    """Deterministic expected-evidence window derived from available evidence."""
    if events:
        timestamps = sorted(e.event_timestamp for e in events)
        return timestamps[0], timestamps[-1]
    fallback = case.created_at
    return fallback, fallback


def _detect_order_anomaly(
    events: Sequence[EventModel], tolerance_seconds: float
) -> List[UUID]:
    """Flag downstream legs stamped well *before* upstream legs.

    This is intentionally stricter than Part 3's ±tolerance ambiguity check: it
    only fires when the inversion exceeds the tolerance (i.e. is not mere skew).
    """
    affected: Set[UUID] = set()
    ordered = sorted(events, key=_event_sort_key)
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            e1, e2 = ordered[i], ordered[j]
            r1 = _PARTY_RANK.get(e1.party, 0)
            r2 = _PARTY_RANK.get(e2.party, 0)
            if r1 == r2:
                continue
            upstream, downstream = (e1, e2) if r1 < r2 else (e2, e1)
            delta = (upstream.event_timestamp - downstream.event_timestamp).total_seconds()
            if delta > tolerance_seconds:
                affected.add(upstream.id)
                affected.add(downstream.id)
    return sorted(affected, key=str)


def _build_data_quality_flags(
    correlation: CaseCorrelationResult,
    duplicates: Sequence[EventModel],
    order_anomaly_ids: Sequence[UUID],
) -> List[DataQualityFlagItem]:
    """Derive deterministic audit flags from correlation, duplicates and ordering."""
    flags: List[DataQualityFlagItem] = []

    for group in correlation.groups:
        if group.temporal_ordering_ambiguous:
            flags.append(
                DataQualityFlagItem(
                    flag_code="CLOCK_SKEW_AMBIGUOUS_ORDERING",
                    severity=FlagSeverityEnum.MEDIUM,
                    description=(
                        "Timestamps within the clock-skew tolerance prevent unambiguous "
                        "sequencing of correlated legs."
                    ),
                    affected_event_ids=sorted(group.member_event_ids, key=str),
                    impact="Caps finding strength at MODERATE.",
                )
            )
        if group.status == CorrelationStatusEnum.AMBIGUOUS:
            if group.reason_code == CorrelationReasonCode.CURRENCY_MISMATCH:
                code = "CURRENCY_CONFLICT"
                desc = "Linked records carry contradictory currencies."
            else:
                code = "IDENTIFIER_CONFLICT"
                desc = "Linked records carry contradictory global identifiers."
            flags.append(
                DataQualityFlagItem(
                    flag_code=code,
                    severity=FlagSeverityEnum.CRITICAL,
                    description=f"{desc} {group.reason_description}",
                    affected_event_ids=sorted(group.member_event_ids, key=str),
                    impact="Caps finding strength at MODERATE; linkage is not authoritative.",
                )
            )

    if duplicates:
        flags.append(
            DataQualityFlagItem(
                flag_code="DUPLICATE_EVIDENCE",
                severity=FlagSeverityEnum.LOW,
                description=(
                    "Identical evidence signatures detected; probable duplicate records "
                    "were collapsed to a single canonical leg."
                ),
                affected_event_ids=_sorted_ids(duplicates),
                impact="Provenance preserved; duplicate never treated as a second payment.",
            )
        )

    if order_anomaly_ids:
        flags.append(
            DataQualityFlagItem(
                flag_code="EVENT_ORDER_ANOMALY",
                severity=FlagSeverityEnum.MEDIUM,
                description=(
                    "A downstream leg is timestamped materially earlier than its upstream "
                    "leg beyond the clock-skew tolerance."
                ),
                affected_event_ids=list(order_anomaly_ids),
                impact="Caps finding strength at MODERATE.",
            )
        )

    # Deterministic flag ordering by (severity rank desc, flag_code).
    severity_rank = {
        FlagSeverityEnum.CRITICAL: 0,
        FlagSeverityEnum.MEDIUM: 1,
        FlagSeverityEnum.LOW: 2,
    }
    flags.sort(key=lambda f: (severity_rank[f.severity], f.flag_code))
    return flags


def _cap_strength(
    base: StrengthEnum, flags: Sequence[DataQualityFlagItem]
) -> Tuple[StrengthEnum, Optional[str]]:
    """Cap an otherwise-strong finding at MODERATE when material flags exist."""
    cap_codes = sorted({f.flag_code for f in flags if f.severity in _CAPPING_SEVERITIES})
    if not cap_codes:
        return base, None
    if _STRENGTH_ORDER[base] > _STRENGTH_ORDER[StrengthEnum.MODERATE]:
        return (
            StrengthEnum.MODERATE,
            "Capped at MODERATE by data-quality flags: " + ", ".join(cap_codes) + ".",
        )
    return base, None


class _Finding:
    """Internal, fully-resolved rule outcome prior to response assembly."""

    def __init__(
        self,
        outcome: OutcomeEnum,
        strength: StrengthEnum,
        category: str,
        outcome_reason: str,
        supporting: Sequence[EventModel] = (),
        contradicting: Sequence[EventModel] = (),
        missing_evidence: Optional[List[MissingEvidenceItem]] = None,
        alternative_explanations: Optional[List[str]] = None,
        recommended_next_evidence: Optional[List[RecommendedEvidenceItem]] = None,
    ) -> None:
        self.outcome = outcome
        self.base_strength = strength
        self.category = category
        self.outcome_reason = outcome_reason
        self.supporting = list(supporting)
        self.contradicting = list(contradicting)
        self.missing_evidence = list(missing_evidence or [])
        self.alternative_explanations = list(alternative_explanations or [])
        self.recommended_next_evidence = list(recommended_next_evidence or [])


def _confirmed_groups(correlation: CaseCorrelationResult) -> List[CorrelationGroup]:
    return [g for g in correlation.groups if g.status == CorrelationStatusEnum.CONFIRMED]


def _confirmed_group_chain(
    confirmed: Sequence[CorrelationGroup],
    *legs: Sequence[EventModel],
) -> Optional[Tuple[CorrelationGroup, List[List[EventModel]]]]:
    """Return one authoritative group containing at least one event from each leg.

    Part 3 groups are the only transaction-linkage contract available to Part 4.
    A confirmed relationship is therefore group-local: amount, currency, timing,
    order context, and similar references must not bridge separate groups.  Part 3
    assigns an event to one group and exposes no transaction identifier that
    composes multiple confirmed groups, so this deliberately does not invent a
    cross-group linkage mechanism.
    """
    for group in confirmed:
        member_ids = set(group.member_event_ids)
        matches = [
            sorted((event for event in leg if event.id in member_ids), key=_event_sort_key)
            for leg in legs
        ]
        if all(matches):
            return group, matches
    return None


def _unresolved_identity_cluster(
    events: Sequence[EventModel], correlation: CaseCorrelationResult
) -> bool:
    """True when two or more of ``events`` share a CANDIDATE/AMBIGUOUS group.

    Amount/time proximity or a conflicting-identifier cluster never establishes
    that the members are genuinely distinct transactions.  When several
    successful debits fall inside such a group their identity is unresolved, so
    they cannot be counted as independent double debits.
    """
    ids = {e.id for e in events}
    if len(ids) < 2:
        return False
    for group in correlation.groups:
        if group.status in (
            CorrelationStatusEnum.CANDIDATE,
            CorrelationStatusEnum.AMBIGUOUS,
        ):
            if len(ids & set(group.member_event_ids)) >= 2:
                return True
    return False


def _group_leg_contradiction(
    members: Sequence[EventModel],
) -> Optional[Tuple[List[EventModel], List[EventModel]]]:
    """Return ``(succeeded, failed)`` when one payment leg has both statuses.

    Only claims about the *same* leg (per ``_LEG_BY_ACTION``) can be genuinely
    contradictory.  Successful and failed claims about different legs describe a
    normal cross-stage progression and are deliberately not treated as
    contradictions here.  ``succeeded``/``failed`` are deterministically sorted.
    """
    by_leg: Dict[str, Dict[EventStatusEnum, List[EventModel]]] = {}
    for event in members:
        action = _action(event)
        leg = _LEG_BY_ACTION.get(action)
        if leg is None:
            continue
        by_leg.setdefault(leg, {}).setdefault(_status(event), []).append(event)

    for leg in sorted(by_leg):
        succeeded = by_leg[leg].get(EventStatusEnum.SUCCESS, [])
        failed = by_leg[leg].get(EventStatusEnum.FAILED, [])
        if succeeded and failed:
            return (
                sorted(succeeded, key=_event_sort_key),
                sorted(failed, key=_event_sort_key),
            )
    return None


def _group_members(
    group: CorrelationGroup, by_id: Dict[UUID, EventModel]
) -> List[EventModel]:
    return [by_id[eid] for eid in group.member_event_ids if eid in by_id]


def _first_duplicate_recommendation() -> RecommendedEvidenceItem:
    return RecommendedEvidenceItem(
        evidence_type="SOURCE_FILE_REUPLOAD",
        expected_party=None,
        rationale=(
            "Identical evidence signatures were observed; confirm the authoritative "
            "source file to eliminate double-counting risk."
        ),
        related_event_ids=[],
    )


def _evaluate_rules(
    case: CaseModel,
    canonical: Sequence[EventModel],
    correlation: CaseCorrelationResult,
) -> _Finding:
    """Apply the deterministic rule hierarchy in strict precedence order."""
    by_id = _events_by_id(canonical)

    def act(e: EventModel) -> EventActionEnum:
        return _action(e)

    def stat(e: EventModel) -> EventStatusEnum:
        return _status(e)

    debits = [e for e in canonical if act(e) == EventActionEnum.DEBIT]
    success_debits = [e for e in debits if stat(e) == EventStatusEnum.SUCCESS]
    success_credits = [
        e for e in canonical if act(e) == EventActionEnum.CREDIT and stat(e) == EventStatusEnum.SUCCESS
    ]
    failed_credits = [
        e for e in canonical if act(e) == EventActionEnum.CREDIT and stat(e) == EventStatusEnum.FAILED
    ]
    rb_success_credits = [
        e for e in success_credits if _party(e) == InstitutionPartyEnum.RECEIVING_BANK
    ]
    downstream_success = [
        e
        for e in canonical
        if stat(e) == EventStatusEnum.SUCCESS
        and act(e) in (EventActionEnum.SWITCH_IN, EventActionEnum.SWITCH_OUT, EventActionEnum.ACK)
    ]
    system_reversals = [
        e
        for e in canonical
        if act(e) == EventActionEnum.REVERSAL_ATTEMPT
        and stat(e) == EventStatusEnum.SUCCESS
        and _party(e) in (InstitutionPartyEnum.ORIGINATING_BANK, InstitutionPartyEnum.SWITCH)
    ]
    merchant_refunds = [
        e
        for e in canonical
        if _party(e) == InstitutionPartyEnum.MERCHANT
        and stat(e) == EventStatusEnum.SUCCESS
        and act(e) in (EventActionEnum.ACK, EventActionEnum.CREDIT)
    ]
    timeouts = [
        e for e in canonical if act(e) == EventActionEnum.TIMEOUT or stat(e) == EventStatusEnum.TIMEOUT
    ]
    success_switch = [
        e
        for e in canonical
        if act(e) in (EventActionEnum.SWITCH_IN, EventActionEnum.SWITCH_OUT)
        and stat(e) == EventStatusEnum.SUCCESS
    ]
    #: Successful legs that precede settlement - the sender side of the chain.
    success_upstream = success_debits + success_switch

    confirmed = _confirmed_groups(correlation)

    # -------------------------------------------------------------------------
    # (A) Genuine contradiction: the SAME payment leg reports incompatible
    #     terminal statuses within one authoritative CONFIRMED group.
    #
    #     A SUCCESS on one leg and a FAILED on a different leg (e.g. sender debit
    #     SUCCESS vs receiving-bank credit FAILED) is a normal cross-stage
    #     sequence, not a contradiction, and is handled by later rules.
    # -------------------------------------------------------------------------
    for group in confirmed:
        members = _group_members(group, by_id)
        contradiction = _group_leg_contradiction(members)
        if contradiction is None:
            continue
        succeeded, failed = contradiction
        conflicting_leg = _LEG_BY_ACTION[_action(succeeded[0])]
        return _Finding(
            outcome=OutcomeEnum.CONFLICTING_EVIDENCE,
            strength=StrengthEnum.LOW,
            category="CONTRADICTORY_TERMINAL_STATUS",
            outcome_reason=(
                "The same authoritatively linked payment leg "
                f"('{conflicting_leg}') reports both SUCCESS and FAILED terminal "
                "claims that cannot be reconciled from the available evidence."
            ),
            supporting=succeeded,
            contradicting=failed,
        )

    # -------------------------------------------------------------------------
    # (B) Linked amount mismatch inside an authoritative CONFIRMED group.
    # -------------------------------------------------------------------------
    for group in confirmed:
        members = _group_members(group, by_id)
        debit_amounts = {
            e.amount_minor_units
            for e in members
            if act(e) == EventActionEnum.DEBIT and stat(e) == EventStatusEnum.SUCCESS
        }
        credit_amounts = {
            e.amount_minor_units
            for e in members
            if act(e) == EventActionEnum.CREDIT and stat(e) == EventStatusEnum.SUCCESS
        }
        if debit_amounts and credit_amounts and (debit_amounts | credit_amounts) != debit_amounts:
            involved = [
                e
                for e in members
                if (act(e) == EventActionEnum.DEBIT and stat(e) == EventStatusEnum.SUCCESS)
                or (act(e) == EventActionEnum.CREDIT and stat(e) == EventStatusEnum.SUCCESS)
            ]
            return _Finding(
                outcome=OutcomeEnum.DISCREPANCY_CONFIRMED,
                strength=StrengthEnum.HIGH,
                category="SETTLEMENT_AMOUNT_MISMATCH",
                outcome_reason=(
                    "Authoritatively linked adjusted debit and credit legs carry divergent "
                    f"amounts ({sorted(debit_amounts)} vs {sorted(credit_amounts)}), confirming "
                    "a settlement discrepancy."
                ),
                supporting=involved,
            )

    # -------------------------------------------------------------------------
    # (C) Multiple distinct successful sender debits with no reversal/refund.
    # -------------------------------------------------------------------------
    debits_identity_unresolved = _unresolved_identity_cluster(success_debits, correlation)
    if (
        len(success_debits) >= 2
        and not system_reversals
        and not merchant_refunds
        and not debits_identity_unresolved
    ):
        return _Finding(
            outcome=OutcomeEnum.DISCREPANCY_CONFIRMED,
            strength=StrengthEnum.MODERATE,
            category="MULTIPLE_UNREVERSED_DEBITS",
            outcome_reason=(
                f"{len(success_debits)} distinct successful sender debits were recorded with "
                "no successful system reversal or merchant refund, confirming a duplicated "
                "or unsettled debit."
            ),
            supporting=success_debits,
        )

    # -------------------------------------------------------------------------
    # (D) Successful upstream leg followed by a downstream timeout, no settlement.
    #
    #     A cross-stage sequence (sender/transit succeeded, downstream did not
    #     respond): the timeout is affirmative evidence of a likely downstream
    #     failure mechanism.  It is preserved and the final state is requested.
    # -------------------------------------------------------------------------
    def _upstream_contradictions() -> List[EventModel]:
        upstream_ids = {e.id for e in success_upstream}
        return [e for e in downstream_success if e.id not in upstream_ids]

    def _final_state_missing(
        evidence_type: str,
        expected_party: InstitutionPartyEnum,
        expected_action: EventActionEnum,
        reason: str,
        request: str,
    ) -> List[MissingEvidenceItem]:
        win_start, win_end = _evidence_window(canonical, case)
        return [
            MissingEvidenceItem(
                expected_party=expected_party,
                expected_action=expected_action,
                window_start=win_start,
                window_end=win_end,
                reason=reason,
                evidence_type=evidence_type,
                description=(
                    "Authoritative record establishing the final state of the downstream leg."
                ),
                impact="Required to establish the final transaction state.",
                recommended_request=request,
            )
        ]

    timeout_chain = _confirmed_group_chain(confirmed, success_upstream, timeouts)
    if timeout_chain and not success_credits:
        _group, (linked_upstream, linked_timeouts) = timeout_chain
        return _Finding(
            outcome=OutcomeEnum.LIKELY_ROOT_CAUSE,
            strength=StrengthEnum.MODERATE,
            category="DOWNSTREAM_TIMEOUT_NO_SETTLEMENT",
            outcome_reason=(
                "A successful upstream leg is followed by a downstream timeout with no "
                "successful receiving-bank credit, identifying an unresolved downstream leg "
                "as the likely root cause; the final transaction state is not yet established."
            ),
            # A failed credit corroborates downstream failure; a downstream SUCCESS
            # that is not itself part of the upstream chain contradicts the timeout.
            # Preserve all observed failed-credit records as evidence without
            # treating an unlinked one as part of the timeout attribution.
            supporting=linked_upstream + linked_timeouts + failed_credits,
            contradicting=_upstream_contradictions(),
            missing_evidence=_final_state_missing(
                "SETTLEMENT_RESOLUTION",
                InstitutionPartyEnum.RECEIVING_BANK,
                EventActionEnum.CREDIT,
                "The downstream timeout leaves the settlement outcome unresolved.",
                "Request the receiving-bank ledger for the transaction window.",
            ),
            recommended_next_evidence=[
                RecommendedEvidenceItem(
                    evidence_type="SETTLEMENT_RESOLUTION",
                    expected_party=InstitutionPartyEnum.RECEIVING_BANK,
                    rationale=(
                        "A final credit or failure record would establish whether the funds "
                        "settled or were lost."
                    ),
                    related_event_ids=_sorted_ids(linked_upstream + linked_timeouts),
                )
            ],
        )

    # -------------------------------------------------------------------------
    # (D2) Successful upstream leg followed by an explicit downstream FAILED
    #      credit, no settlement.  The failed credit is affirmative evidence of a
    #      downstream failure mechanism (not a contradiction) and is preserved.
    # -------------------------------------------------------------------------
    failed_credit_chain = _confirmed_group_chain(
        confirmed, success_upstream, failed_credits
    )
    if failed_credit_chain and not success_credits:
        _group, (linked_upstream, linked_failed_credits) = failed_credit_chain
        return _Finding(
            outcome=OutcomeEnum.LIKELY_ROOT_CAUSE,
            strength=StrengthEnum.MODERATE,
            category="DOWNSTREAM_FAILURE_NO_SETTLEMENT",
            outcome_reason=(
                "A successful upstream leg is linked to a FAILED receiving-bank credit with "
                "no successful settlement, identifying the receiving-bank leg as the likely "
                "failure mechanism; the final disposition is not yet established."
            ),
            supporting=linked_upstream + linked_failed_credits,
            contradicting=_upstream_contradictions(),
            missing_evidence=_final_state_missing(
                "REVERSAL_RECORD",
                InstitutionPartyEnum.ORIGINATING_BANK,
                EventActionEnum.REVERSAL_ATTEMPT,
                "The failed credit does not establish whether the sender debit was "
                "reversed or refunded.",
                "Request the originating bank / switch reversal journal for the session.",
            ),
            recommended_next_evidence=[
                RecommendedEvidenceItem(
                    evidence_type="REVERSAL_RECORD",
                    expected_party=InstitutionPartyEnum.ORIGINATING_BANK,
                    rationale=(
                        "An authoritative reversal/refund record would establish the final "
                        "transaction state after the downstream failure."
                    ),
                    related_event_ids=_sorted_ids(linked_upstream + linked_failed_credits),
                )
            ],
        )

    # -------------------------------------------------------------------------
    # (D3) System reversal AND independently successful downstream value movement.
    #      Funds cannot be both returned to the sender and delivered downstream;
    #      the two material claims are genuinely incompatible.
    # -------------------------------------------------------------------------
    downstream_value = success_credits + merchant_refunds
    reversal_chain = _confirmed_group_chain(
        confirmed, success_debits, system_reversals, downstream_value
    )
    if reversal_chain:
        _group, (linked_debits, linked_reversals, linked_downstream_value) = reversal_chain
        return _Finding(
            outcome=OutcomeEnum.CONFLICTING_EVIDENCE,
            strength=StrengthEnum.LOW,
            category="REVERSAL_SETTLEMENT_CONFLICT",
            outcome_reason=(
                "A successful system reversal of the sender debit is contradicted by "
                "independently successful downstream value movement; both claims cannot be "
                "true and cannot be resolved from the available evidence."
            ),
            supporting=linked_debits + linked_reversals,
            contradicting=linked_downstream_value,
        )

    # -------------------------------------------------------------------------
    # (D0) Material D/D2/D3 records exist, but Part 3 does not confirm that
    #      they describe one transaction.  Preserve every such record as
    #      evidence, but do not attribute a transaction-specific root cause.
    # -------------------------------------------------------------------------
    unresolved_d_legs: List[EventModel] = []
    if success_upstream and timeouts and not timeout_chain:
        unresolved_d_legs.extend(success_upstream + timeouts)
    if success_upstream and failed_credits and not failed_credit_chain:
        unresolved_d_legs.extend(success_upstream + failed_credits)
    if success_debits and system_reversals and downstream_value and not reversal_chain:
        unresolved_d_legs.extend(success_debits + system_reversals + downstream_value)
    if unresolved_d_legs:
        win_start, win_end = _evidence_window(canonical, case)
        preserved = sorted({event.id: event for event in unresolved_d_legs}.values(), key=_event_sort_key)
        return _Finding(
            outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE,
            strength=StrengthEnum.INSUFFICIENT,
            category="UNRESOLVED_TRANSACTION_LINKAGE",
            outcome_reason=(
                "Timeout, failed-credit, or reversal evidence is present, but Part 3 does "
                "not provide a CONFIRMED group containing every material leg of the claimed "
                "transaction chain. No transaction-specific attribution can be made."
            ),
            supporting=preserved,
            missing_evidence=[
                MissingEvidenceItem(
                    expected_party=InstitutionPartyEnum.SWITCH,
                    expected_action=EventActionEnum.SWITCH_IN,
                    window_start=win_start,
                    window_end=win_end,
                    reason=(
                        "The material records are not authoritatively linked to one "
                        "transaction by a Part 3 CONFIRMED correlation group."
                    ),
                    evidence_type="CORRELATION_IDENTIFIER",
                    description=(
                        "Verified global session_id / RRN evidence connecting every "
                        "material transaction leg."
                    ),
                    impact="Required before attributing the timeout, failure, or reversal.",
                    recommended_request=(
                        "Request the switch and participating-institution journals carrying "
                        "the global session_id / RRN for the affected records."
                    ),
                )
            ],
            alternative_explanations=[
                "The records may belong to different transactions.",
                "A failed or timed-out attempt may have been retried or recovered separately.",
            ],
            recommended_next_evidence=[
                RecommendedEvidenceItem(
                    evidence_type="CORRELATION_IDENTIFIER",
                    expected_party=InstitutionPartyEnum.SWITCH,
                    rationale=(
                        "A verified global identifier is needed to confirm or exclude a "
                        "transaction relationship among the preserved records."
                    ),
                    related_event_ids=_sorted_ids(preserved),
                )
            ],
        )

    # -------------------------------------------------------------------------
    # (E) Debit reversed by the system -> no outstanding discrepancy.
    # -------------------------------------------------------------------------
    if success_debits and system_reversals:
        return _Finding(
            outcome=OutcomeEnum.NO_DISCREPANCY_FOUND,
            strength=StrengthEnum.MODERATE,
            category="DEBIT_REVERSED_SETTLED",
            outcome_reason=(
                "The sender debit is matched by a successful system reversal, restoring the "
                "account to its pre-transaction position; no outstanding discrepancy remains."
            ),
            supporting=success_debits + system_reversals,
        )

    # -------------------------------------------------------------------------
    # (E2) Merchant-side refund without an authoritative system reversal.
    # -------------------------------------------------------------------------
    if success_debits and merchant_refunds and not system_reversals and not rb_success_credits:
        win_start, win_end = _evidence_window(canonical, case)
        return _Finding(
            outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE,
            strength=StrengthEnum.INSUFFICIENT,
            category="MERCHANT_REFUND_UNVERIFIED",
            outcome_reason=(
                "A merchant-side credit/acknowledgement is present, but no authoritative "
                "system reversal exists, so the refund and the debit cannot be reconciled "
                "into a single settlement chain."
            ),
            supporting=success_debits + merchant_refunds,
            missing_evidence=[
                MissingEvidenceItem(
                    expected_party=InstitutionPartyEnum.ORIGINATING_BANK,
                    expected_action=EventActionEnum.REVERSAL_ATTEMPT,
                    window_start=win_start,
                    window_end=win_end,
                    reason=(
                        "A merchant-side refund without an authoritative system reversal "
                        "cannot establish whether the original debit was reversed."
                    ),
                    evidence_type="REVERSAL_RECORD",
                    description="Authoritative system reversal of the original sender debit.",
                    impact="Required to determine whether the debit was reversed.",
                    recommended_request=(
                        "Request the originating bank / switch reversal journal for the "
                        "original session."
                    ),
                )
            ],
            recommended_next_evidence=[
                RecommendedEvidenceItem(
                    evidence_type="REVERSAL_RECORD",
                    expected_party=InstitutionPartyEnum.ORIGINATING_BANK,
                    rationale="An authoritative reversal would confirm the debit was undone.",
                    related_event_ids=_sorted_ids(success_debits),
                )
            ],
        )

    # -------------------------------------------------------------------------
    # (F) Clean, authoritatively linked debit -> credit settlement.
    # -------------------------------------------------------------------------
    no_failed_core = not any(
        stat(e) == EventStatusEnum.FAILED for e in canonical if act(e) in _CORE_ACTIONS
    )
    for group in confirmed:
        members = _group_members(group, by_id)
        group_debits = [
            e for e in members if act(e) == EventActionEnum.DEBIT and stat(e) == EventStatusEnum.SUCCESS
        ]
        group_credits = [
            e
            for e in members
            if act(e) == EventActionEnum.CREDIT
            and stat(e) == EventStatusEnum.SUCCESS
            and _party(e) == InstitutionPartyEnum.RECEIVING_BANK
        ]
        if group_debits and group_credits and no_failed_core:
            amounts = {e.amount_minor_units for e in group_debits + group_credits}
            if len(amounts) == 1:
                return _Finding(
                    outcome=OutcomeEnum.NO_DISCREPANCY_FOUND,
                    strength=StrengthEnum.HIGH,
                    category="SETTLED_CONSISTENT",
                    outcome_reason=(
                        "An authoritatively linked, amount-consistent debit-to-credit chain "
                        "with no failed legs reconstructs a fully settled transaction."
                    ),
                    supporting=members,
                )

    # -------------------------------------------------------------------------
    # (G) Downstream success but no receiving-bank settlement.
    # -------------------------------------------------------------------------
    if success_debits and downstream_success and not rb_success_credits:
        win_start, win_end = _evidence_window(canonical, case)
        return _Finding(
            outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE,
            strength=StrengthEnum.INSUFFICIENT,
            category="DOWNSTREAM_SUCCESS_NO_SETTLEMENT",
            outcome_reason=(
                "Downstream success legs exist, but no receiving-bank credit confirms that "
                "funds were ever settled to the beneficiary."
            ),
            supporting=success_debits + downstream_success,
            missing_evidence=[
                MissingEvidenceItem(
                    expected_party=InstitutionPartyEnum.RECEIVING_BANK,
                    expected_action=EventActionEnum.CREDIT,
                    window_start=win_start,
                    window_end=win_end,
                    reason="No receiving-bank credit record corroborates settlement.",
                    evidence_type="SETTLEMENT_CREDIT",
                    description="Receiving-bank credit posting for the transaction.",
                    impact="Required to establish whether settlement completed.",
                    recommended_request="Request the receiving-bank ledger for the session.",
                )
            ],
            recommended_next_evidence=[
                RecommendedEvidenceItem(
                    evidence_type="SETTLEMENT_CREDIT",
                    expected_party=InstitutionPartyEnum.RECEIVING_BANK,
                    rationale="A credit posting would confirm settlement completion.",
                    related_event_ids=_sorted_ids(success_debits),
                )
            ],
        )

    # -------------------------------------------------------------------------
    # (H) Similar amount/currency evidence that is not authoritatively linked.
    # -------------------------------------------------------------------------
    buckets: Dict[Tuple[int, str], List[EventModel]] = {}
    for e in canonical:
        buckets.setdefault((e.amount_minor_units, e.currency), []).append(e)
    for key in sorted(buckets):
        members = buckets[key]
        if len(members) < 2:
            continue
        linked = False
        for group in confirmed:
            group_ids = set(group.member_event_ids)
            if sum(1 for m in members if m.id in group_ids) >= 2:
                linked = True
                break
        if not linked:
            win_start, win_end = _evidence_window(canonical, case)
            return _Finding(
                outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE,
                # DOCUMENTED EXCEPTION (§1.2 / §5.2 rule H): the transaction
                # conclusion is INSUFFICIENT, but the co-located amount/currency
                # values constitute a weak *directional* pattern of a possible
                # linkage, which is what the LOW strength label describes.
                strength=StrengthEnum.LOW,
                category="UNLINKED_SIMILAR_EVIDENCE",
                outcome_reason=(
                    "Records share amount and currency but are not authoritatively linked by "
                    "any verified unique identifier, so a discrepancy cannot be confirmed or "
                    "excluded."
                ),
                supporting=members,
                missing_evidence=[
                    MissingEvidenceItem(
                        expected_party=InstitutionPartyEnum.SWITCH,
                        expected_action=EventActionEnum.SWITCH_IN,
                        window_start=win_start,
                        window_end=win_end,
                        reason=(
                            "No authoritative global identifier links the similar records, so "
                            "their distinctness cannot be established."
                        ),
                        evidence_type="CORRELATION_IDENTIFIER",
                        description=(
                            "Authoritative interbank session_id / RRN linking the similar records."
                        ),
                        impact="Required to determine whether the records are one or many transactions.",
                        recommended_request=(
                            "Request the switch journal or originating bank records carrying the "
                            "global session_id / RRN for the affected window."
                        ),
                    )
                ],
                recommended_next_evidence=[
                    RecommendedEvidenceItem(
                        evidence_type="CORRELATION_IDENTIFIER",
                        expected_party=InstitutionPartyEnum.SWITCH,
                        rationale=(
                            "A verified global identifier would confirm or exclude a link between "
                            "the similar records."
                        ),
                        related_event_ids=_sorted_ids(members),
                    )
                ],
                alternative_explanations=[
                    "The records may describe the same transaction reported by different parties.",
                    "The records may describe genuinely distinct transactions that coincidentally share an amount.",
                    "A failed attempt may have been retried or recovered under a different transaction identity.",
                ],
            )

    # -------------------------------------------------------------------------
    # (I) Fallback: insufficient evidence to reach any determination.
    # -------------------------------------------------------------------------
    win_start, win_end = _evidence_window(canonical, case)
    return _Finding(
        outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE,
        strength=StrengthEnum.INSUFFICIENT,
        category="INSUFFICIENT_EVIDENCE",
        outcome_reason=(
            "Available evidence does not contain an authoritatively linked chain of money "
            "movement sufficient to reach any deterministic determination."
        ),
        supporting=canonical,
        missing_evidence=[
            MissingEvidenceItem(
                expected_party=InstitutionPartyEnum.ORIGINATING_BANK,
                expected_action=EventActionEnum.DEBIT,
                window_start=win_start,
                window_end=win_end,
                reason=(
                    "No complete, authoritatively linked debit-to-credit chain was submitted."
                ),
                evidence_type="TRANSACTION_CHAIN",
                description=(
                    "The full set of legs (sender debit, switch, receiving-bank credit) for the "
                    "disputed transaction."
                ),
                impact="Required to reconstruct the money-movement chain.",
                recommended_request=(
                    "Request the complete transaction chain from each participating institution."
                ),
            )
        ],
        recommended_next_evidence=[
            RecommendedEvidenceItem(
                evidence_type="TRANSACTION_CHAIN",
                expected_party=None,
                rationale=(
                    "A complete, linked chain of legs is required before any deterministic "
                    "determination can be made."
                ),
                related_event_ids=_sorted_ids(canonical),
            )
        ],
        alternative_explanations=[
            "Additional legs may exist but were not submitted with the evidence.",
        ],
    )


def _build_counterfactuals(finding: _Finding) -> List[CounterfactualItem]:
    """Deterministic counterfactual sensitivity statements per outcome."""
    if finding.outcome == OutcomeEnum.DISCREPANCY_CONFIRMED:
        return [
            CounterfactualItem(
                scenario="Reversal evidence produced",
                condition="A successful system reversal of the debit is submitted.",
                outcome_if_condition_met=OutcomeEnum.NO_DISCREPANCY_FOUND,
                explanation="A confirmed reversal would restore the account and remove the discrepancy.",
            )
        ]
    if finding.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND:
        return [
            CounterfactualItem(
                scenario="Contradictory terminal leg produced",
                condition="An authoritatively linked FAILED leg is produced.",
                outcome_if_condition_met=OutcomeEnum.CONFLICTING_EVIDENCE,
                explanation="A contradictory terminal status would invalidate the settled chain.",
            )
        ]
    if finding.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE:
        return [
            CounterfactualItem(
                scenario="Late settlement confirmed",
                condition="A successful receiving-bank credit is produced for the session.",
                outcome_if_condition_met=OutcomeEnum.NO_DISCREPANCY_FOUND,
                explanation="A confirmed late credit would reclassify the event as a delay, not a loss.",
            )
        ]
    if finding.outcome == OutcomeEnum.CONFLICTING_EVIDENCE:
        return [
            CounterfactualItem(
                scenario="Contradiction resolved",
                condition="The conflicting terminal statuses are corrected to a single value.",
                outcome_if_condition_met=OutcomeEnum.DISCREPANCY_CONFIRMED,
                explanation="A consistent terminal status would allow a settled or discrepancy finding.",
            )
        ]
    return [
        CounterfactualItem(
            scenario="Decisive evidence produced",
            condition="Authoritative missing legs are submitted for the transaction.",
            outcome_if_condition_met=OutcomeEnum.DISCREPANCY_CONFIRMED,
            explanation="Additional authoritative evidence could confirm or exclude a discrepancy.",
        )
    ]


def _enforce_insufficient_evidence_contract(
    finding: _Finding, case: CaseModel, canonical: Sequence[EventModel]
) -> None:
    """Globally enforce the evidence/strength contract for insufficient findings.

    Rule H (Scenario 8) is the sole documented exception: it retains LOW to
    communicate a weak directional pattern while the outcome remains
    INSUFFICIENT_EVIDENCE.  Every other insufficient finding is INSUFFICIENT
    strength and includes both a missing-evidence request and next action.
    """
    if finding.outcome != OutcomeEnum.INSUFFICIENT_EVIDENCE:
        return

    if finding.category == "UNLINKED_SIMILAR_EVIDENCE":
        finding.base_strength = StrengthEnum.LOW
    else:
        finding.base_strength = StrengthEnum.INSUFFICIENT

    win_start, win_end = _evidence_window(canonical, case)
    if not finding.missing_evidence:
        finding.missing_evidence.append(
            MissingEvidenceItem(
                expected_party=InstitutionPartyEnum.SWITCH,
                expected_action=EventActionEnum.SWITCH_IN,
                window_start=win_start,
                window_end=win_end,
                reason="The submitted evidence is insufficient for a deterministic determination.",
                evidence_type="TRANSACTION_CHAIN",
                description="Authoritative records completing the transaction chain.",
                impact="Required to determine the transaction outcome.",
                recommended_request="Request the complete transaction chain from all parties.",
            )
        )
    if not finding.recommended_next_evidence:
        finding.recommended_next_evidence.append(
            RecommendedEvidenceItem(
                evidence_type="TRANSACTION_CHAIN",
                expected_party=None,
                rationale="Authoritative missing legs are required for a deterministic outcome.",
                related_event_ids=_sorted_ids(canonical),
            )
        )


def evaluate_case(
    case: CaseModel,
    events: Sequence[EventModel],
    correlation: Optional[CaseCorrelationResult] = None,
) -> AnalysisResponse:
    """Deterministically evaluate a case and return its analysis contract.

    Parameters
    ----------
    case:
        Persisted dispute case being investigated.
    events:
        Canonical normalized events belonging to the case (Part 2 output).
    correlation:
        Optional pre-computed Part 3 result.  When omitted, correlation is
        computed deterministically over the de-duplicated canonical legs.
    """
    canonical, duplicates = _deduplicate(events)
    tolerance = settings.CLOCK_SKEW_TOLERANCE_SECONDS

    if correlation is None:
        correlation = correlate_events(
            events=list(canonical), case_id=case.id, tolerance_seconds=tolerance
        )

    order_anomaly_ids = _detect_order_anomaly(canonical, tolerance)
    flags = _build_data_quality_flags(correlation, duplicates, order_anomaly_ids)

    finding = _evaluate_rules(case, canonical, correlation)
    _enforce_insufficient_evidence_contract(finding, case, canonical)

    final_strength, cap_reason = _cap_strength(finding.base_strength, flags)
    strength_reason = cap_reason or (
        f"Base strength {finding.base_strength.value} follows directly from rule "
        f"'{finding.category}' with no capping data-quality flags present."
    )

    summary = correlation.summary
    scope_examined = (
        f"{len(canonical)} canonical event(s) "
        f"({len(duplicates)} probable duplicate(s) collapsed) across "
        f"{len({e.party for e in canonical})} institution party(ies); "
        f"{summary.get('CONFIRMED', 0)} confirmed, {summary.get('AMBIGUOUS', 0)} ambiguous, "
        f"{summary.get('CANDIDATE', 0)} candidate, {summary.get('UNMATCHED', 0)} unmatched "
        "correlation group(s)."
    )

    recommendations = list(finding.recommended_next_evidence)
    if duplicates and not any(
        r.evidence_type == "SOURCE_FILE_REUPLOAD" for r in recommendations
    ):
        recommendations.append(_first_duplicate_recommendation())

    return AnalysisResponse(
        case_id=case.id,
        outcome=finding.outcome,
        strength=final_strength,
        rule_version=RULE_VERSION,
        category=finding.category,
        timeline=_build_timeline(canonical),
        supporting_event_ids=_sorted_ids(finding.supporting),
        missing_evidence=finding.missing_evidence,
        data_quality_flags=flags,
        counterfactuals=_build_counterfactuals(finding),
        outcome_reason=finding.outcome_reason,
        strength_reason=strength_reason,
        scope_examined=scope_examined,
        supporting_evidence=_sorted_ids(finding.supporting),
        contradicting_evidence=_sorted_ids(finding.contradicting),
        alternative_explanations=finding.alternative_explanations,
        recommended_next_evidence=recommendations,
    )
