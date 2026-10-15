"""Deterministic Transaction Correlation Engine.

Implements the multi-pass correlation hierarchy defined for TRACE:

1. **Strong match** - events unioned through shared GLOBAL interbank identifiers
   (``session_id`` and/or ``rrn``).  A connected cluster is CONFIRMED only when
   every non-null identifier value within it is mutually consistent; otherwise
   the cluster is AMBIGUOUS (contradictory identifier evidence).
2. **Secondary match** - institution-scoped ``reference``.  A bare reference is
   never assumed to be globally unique; only a cross-source shared reference with
   a verified, single currency/amount pair may be confirmed.
3. **Candidate match** - equal amount, equal currency and timestamp proximity
   within the clock-skew tolerance.  These attributes may never confirm a match
   and are always surfaced as CANDIDATE for human review.
4. **Unmatched** - insufficient identifying evidence or material conflict.

CRITICAL INVARIANTS:
- Deterministic: identical normalized events produce identical groups.  All
  set-derived values are emitted in sorted order so Python hash randomization
  cannot affect output.
- Pure: does NOT mutate source events, raw records, or database state.
- Scoped: rejects cross-case correlation by default.
- Honest: contradictory identifiers are flagged AMBIGUOUS and never silently
  resolved; transitive identifier conflicts across a connected cluster surface.
- Temporal: source timestamps are preserved verbatim; ambiguous sequencing is
  flagged, never rewritten.
- Bounded: does NOT determine transaction success, failure, reversals, or any
  dispute outcome.  It never inspects ``status`` or ``action`` to decide linkage.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID
import uuid

from app.correlation.exceptions import CrossCaseCorrelationError
from app.db.models import EventModel
from app.schemas.correlation import (
    CaseCorrelationResult,
    CorrelationEventRef,
    CorrelationGroup,
    CorrelationMethodEnum,
    CorrelationReasonCode,
    CorrelationStatusEnum,
    IdentifierScopeEnum,
)
from app.schemas.enums import EventActionEnum, EventStatusEnum, InstitutionPartyEnum

ALGORITHM_VERSION = "2026.1.0-corr"

# Logical party progression used only to reason about temporal ordering ambiguity.
PARTY_PROGRESSION: Dict[str, int] = {
    InstitutionPartyEnum.ORIGINATING_BANK.value: 0,
    InstitutionPartyEnum.SWITCH.value: 1,
    InstitutionPartyEnum.RECEIVING_BANK.value: 2,
    InstitutionPartyEnum.MERCHANT.value: 2,
}


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalize an identifier: strip whitespace, collapse blanks to None."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _event_sort_key(event: EventModel) -> Tuple[datetime, str, str]:
    """Deterministic canonical event ordering: timestamp, party, event_id."""
    return (event.event_timestamp, event.party, str(event.id))


def to_event_ref(event: EventModel) -> CorrelationEventRef:
    """Convert an EventModel into a typed CorrelationEventRef preserving provenance."""
    return CorrelationEventRef(
        event_id=event.id,
        raw_record_id=event.raw_record_id,
        party=InstitutionPartyEnum(event.party),
        action=EventActionEnum(event.action),
        status=EventStatusEnum(event.status),
        amount_minor_units=event.amount_minor_units,
        currency=event.currency,
        session_id=event.session_id,
        rrn=event.rrn,
        reference=event.reference,
        event_timestamp=event.event_timestamp,
    )


def generate_deterministic_group_id(case_id: UUID, method: str, member_event_ids: List[UUID]) -> str:
    """Generate a stable, reproducible UUID for a correlation group."""
    sorted_ids_str = ":".join(sorted(str(eid) for eid in member_event_ids))
    seed = f"{case_id}:{method}:{sorted_ids_str}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def is_temporal_ordering_ambiguous(events: List[EventModel], tolerance_seconds: float = 5.0) -> bool:
    """Detect whether timestamps within the group make ordering ambiguous.

    Logical leg progression: ORIGINATING_BANK -> SWITCH -> RECEIVING_BANK/MERCHANT.
    Ordering is ambiguous when (a) a downstream leg is stamped before an upstream
    leg within tolerance, or (b) two legs differ by less than 0.1s.
    """
    if len(events) <= 1:
        return False

    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            e1 = events[i]
            e2 = events[j]
            delta = abs((e1.event_timestamp - e2.event_timestamp).total_seconds())
            if delta > tolerance_seconds:
                continue

            p1_rank = PARTY_PROGRESSION.get(e1.party, 0)
            p2_rank = PARTY_PROGRESSION.get(e2.party, 0)
            if p1_rank != p2_rank:
                if (p1_rank < p2_rank and e1.event_timestamp > e2.event_timestamp) or (
                    p2_rank < p1_rank and e2.event_timestamp > e1.event_timestamp
                ):
                    return True
                if delta < 0.1:
                    return True

    return False


def _find_duplicate_identity_event_ids(events: List[EventModel]) -> List[UUID]:
    """Identify probable duplicate records sharing an identical evidence signature.

    Duplicate identity is *flagged only*; it never mutates status or source data.
    """
    signatures: Dict[Tuple[Any, ...], List[UUID]] = defaultdict(list)
    for event in events:
        signature = (
            event.party,
            event.action,
            event.amount_minor_units,
            event.currency,
            _clean(event.session_id),
            _clean(event.rrn),
            _clean(event.reference),
            event.event_timestamp.isoformat(),
        )
        signatures[signature].append(event.id)

    duplicate_ids: Set[UUID] = set()
    for ids in signatures.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)
    return sorted(duplicate_ids, key=str)


def build_group(
    case_id: UUID,
    status: CorrelationStatusEnum,
    method: CorrelationMethodEnum,
    reason_code: CorrelationReasonCode,
    reason_desc: str,
    events: List[EventModel],
    identifying_keys: Dict[str, Any],
    identifier_scope: IdentifierScopeEnum = IdentifierScopeEnum.NONE,
    contradictions: Optional[List[str]] = None,
    tolerance_seconds: float = 5.0,
) -> CorrelationGroup:
    """Construct a deterministically sorted CorrelationGroup."""
    sorted_events = sorted(events, key=_event_sort_key)
    member_ids = [e.id for e in sorted_events]
    event_refs = [to_event_ref(e) for e in sorted_events]

    group_id = generate_deterministic_group_id(case_id, method.value, member_ids)
    temporal_ambiguous = is_temporal_ordering_ambiguous(sorted_events, tolerance_seconds)
    duplicate_ids = _find_duplicate_identity_event_ids(sorted_events)

    resolved_contradictions = list(contradictions or [])
    if duplicate_ids:
        resolved_contradictions.append(
            "Duplicate identity signature detected across event IDs: "
            + ", ".join(str(eid) for eid in duplicate_ids)
        )

    return CorrelationGroup(
        group_id=group_id,
        status=status,
        method=method,
        identifier_scope=identifier_scope,
        reason_code=reason_code,
        reason_description=reason_desc,
        member_event_ids=member_ids,
        events=event_refs,
        identifying_keys=identifying_keys,
        temporal_ordering_ambiguous=temporal_ambiguous,
        duplicate_identity_event_ids=duplicate_ids,
        contradictions=resolved_contradictions,
    )


class _UnionFind:
    """Deterministic union-find over event IDs (root = lexicographically smallest)."""

    def __init__(self, ids: List[UUID]):
        self._parent: Dict[UUID, UUID] = {i: i for i in ids}

    def find(self, x: UUID) -> UUID:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: UUID, b: UUID) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if str(root_a) <= str(root_b):
            self._parent[root_b] = root_a
        else:
            self._parent[root_a] = root_b


def _build_global_identifier_components(events: List[EventModel]) -> List[List[EventModel]]:
    """Cluster events connected through shared GLOBAL identifiers (session_id / rrn).

    Returns every connected component with more than one member, sorted
    deterministically by earliest member event.
    """
    if not events:
        return []

    union_find = _UnionFind([e.id for e in events])

    session_map: Dict[str, List[EventModel]] = defaultdict(list)
    rrn_map: Dict[str, List[EventModel]] = defaultdict(list)
    for event in events:
        session = _clean(event.session_id)
        if session:
            session_map[session].append(event)
        rrn = _clean(event.rrn)
        if rrn:
            rrn_map[rrn].append(event)

    # Sorted key iteration + first-member anchoring keeps unions deterministic.
    for key in sorted(session_map):
        members = session_map[key]
        for other in members[1:]:
            union_find.union(members[0].id, other.id)
    for key in sorted(rrn_map):
        members = rrn_map[key]
        for other in members[1:]:
            union_find.union(members[0].id, other.id)

    components: Dict[UUID, List[EventModel]] = defaultdict(list)
    for event in events:
        components[union_find.find(event.id)].append(event)

    multi_member = [members for members in components.values() if len(members) > 1]
    multi_member.sort(key=lambda members: min(_event_sort_key(e) for e in members))
    return multi_member


def _classify_global_component(
    component: List[EventModel],
) -> Tuple[str, List[str], List[str], List[str]]:
    """Classify a connected component by its distinct identifier values.

    Returns ``(kind, sessions, rrns, currencies)`` where ``kind`` is one of
    ``"CONFIRMED"``, ``"CONFLICT_IDENTIFIER"`` or ``"CONFLICT_CURRENCY"``.
    """
    sessions = sorted({s for s in (_clean(e.session_id) for e in component) if s})
    rrns = sorted({r for r in (_clean(e.rrn) for e in component) if r})
    currencies = sorted({e.currency for e in component})

    if len(currencies) > 1:
        return "CONFLICT_CURRENCY", sessions, rrns, currencies
    if len(sessions) > 1 or len(rrns) > 1:
        return "CONFLICT_IDENTIFIER", sessions, rrns, currencies
    return "CONFIRMED", sessions, rrns, currencies


def correlate_events(
    events: List[EventModel],
    case_id: Optional[UUID] = None,
    tolerance_seconds: float = 5.0,
) -> CaseCorrelationResult:
    """Execute deterministic multi-pass correlation on normalized events.

    Matches events strictly within a single case boundary.  Never mutates input.
    """
    if not events:
        target_case_id = case_id or uuid.uuid4()
        return CaseCorrelationResult(
            case_id=target_case_id,
            algorithm_version=ALGORITHM_VERSION,
            computed_at=datetime.now(timezone.utc),
            total_events=0,
            groups=[],
            summary={"CONFIRMED": 0, "CANDIDATE": 0, "AMBIGUOUS": 0, "UNMATCHED": 0},
        )

    # 1. Enforce single-case boundary.
    unique_cases = {e.case_id for e in events}
    if len(unique_cases) > 1:
        raise CrossCaseCorrelationError(sorted(unique_cases, key=str))

    resolved_case_id = sorted(unique_cases, key=str)[0]
    if case_id and resolved_case_id != case_id:
        raise CrossCaseCorrelationError(sorted({resolved_case_id, case_id}, key=str))

    # 2. Deterministic baseline ordering.
    sorted_all_events = sorted(events, key=_event_sort_key)

    assigned_event_ids: Set[UUID] = set()
    groups: List[CorrelationGroup] = []

    # =========================================================================
    # PASS 1: Strong match on global interbank identifiers (session_id / rrn)
    # =========================================================================
    for component in _build_global_identifier_components(sorted_all_events):
        kind, sessions, rrns, currencies = _classify_global_component(component)

        if kind == "CONFLICT_CURRENCY":
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.AMBIGUOUS,
                    method=CorrelationMethodEnum.SESSION_AND_RRN_MATCH,
                    identifier_scope=IdentifierScopeEnum.GLOBAL_INTERBANK,
                    reason_code=CorrelationReasonCode.CURRENCY_MISMATCH,
                    reason_desc=(
                        "Events linked by global interbank identifiers possess contradictory "
                        f"currencies: {currencies}. Identifiers may not be silently reconciled."
                    ),
                    events=component,
                    identifying_keys={"currencies": currencies},
                    contradictions=[f"Currencies differ across linked events: {currencies}"],
                    tolerance_seconds=tolerance_seconds,
                )
            )
        elif kind == "CONFLICT_IDENTIFIER":
            contradictions: List[str] = []
            if len(sessions) > 1:
                contradictions.append(f"Conflicting session_ids under linked evidence: {sessions}")
            if len(rrns) > 1:
                contradictions.append(f"Conflicting RRNs under linked evidence: {rrns}")

            if len(sessions) == 1:
                method = CorrelationMethodEnum.SESSION_ID_MATCH
            elif len(rrns) == 1:
                method = CorrelationMethodEnum.RRN_MATCH
            else:
                method = CorrelationMethodEnum.SESSION_AND_RRN_MATCH

            keys: Dict[str, Any] = {}
            if sessions:
                keys["session_ids"] = sessions
            if rrns:
                keys["rrns"] = rrns

            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.AMBIGUOUS,
                    method=method,
                    identifier_scope=IdentifierScopeEnum.GLOBAL_INTERBANK,
                    reason_code=CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT,
                    reason_desc=(
                        "Material identifier conflict across a connected evidence cluster; "
                        "the contradiction is preserved and never silently resolved. "
                        + " ".join(contradictions)
                    ),
                    events=component,
                    identifying_keys=keys,
                    contradictions=contradictions,
                    tolerance_seconds=tolerance_seconds,
                )
            )
        else:
            if len(sessions) == 1 and len(rrns) == 1:
                method = CorrelationMethodEnum.SESSION_AND_RRN_MATCH
                reason = CorrelationReasonCode.STRONG_SESSION_AND_RRN_CONSISTENT
                keys = {"session_id": sessions[0], "rrn": rrns[0]}
                desc = (
                    "Events strongly correlated via a consistent global session_id "
                    f"'{sessions[0]}' and RRN '{rrns[0]}'."
                )
            elif len(sessions) == 1:
                method = CorrelationMethodEnum.SESSION_ID_MATCH
                reason = CorrelationReasonCode.STRONG_SESSION_ID_CONSISTENT
                keys = {"session_id": sessions[0]}
                desc = f"Events strongly correlated via consistent global session_id '{sessions[0]}'."
            else:
                method = CorrelationMethodEnum.RRN_MATCH
                reason = CorrelationReasonCode.STRONG_RRN_CONSISTENT
                keys = {"rrn": rrns[0]}
                desc = f"Events strongly correlated via consistent global RRN '{rrns[0]}'."

            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.CONFIRMED,
                    method=method,
                    identifier_scope=IdentifierScopeEnum.GLOBAL_INTERBANK,
                    reason_code=reason,
                    reason_desc=desc,
                    events=component,
                    identifying_keys=keys,
                    tolerance_seconds=tolerance_seconds,
                )
            )

        for event in component:
            assigned_event_ids.add(event.id)

    # =========================================================================
    # PASS 2: Secondary match on institution-scoped reference
    # =========================================================================
    ref_map: Dict[str, List[EventModel]] = defaultdict(list)
    for event in sorted_all_events:
        if event.id in assigned_event_ids:
            continue
        reference = _clean(event.reference)
        if reference:
            ref_map[reference].append(event)

    for reference in sorted(ref_map):
        cluster = [e for e in ref_map[reference] if e.id not in assigned_event_ids]
        if len(cluster) < 2:
            continue

        parties = sorted({e.party for e in cluster})
        currencies = sorted({e.currency for e in cluster})
        amounts = sorted({e.amount_minor_units for e in cluster})
        sessions = sorted({s for s in (_clean(e.session_id) for e in cluster) if s})
        rrns = sorted({r for r in (_clean(e.rrn) for e in cluster) if r})

        # Never confirm if global identifiers conflict across members.
        if len(sessions) > 1 or len(rrns) > 1:
            contradictions = []
            if len(sessions) > 1:
                contradictions.append(f"Conflicting session_ids under reference '{reference}': {sessions}")
            if len(rrns) > 1:
                contradictions.append(f"Conflicting RRNs under reference '{reference}': {rrns}")
            keys = {"reference": reference, "parties": parties}
            if sessions:
                keys["session_ids"] = sessions
            if rrns:
                keys["rrns"] = rrns
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.AMBIGUOUS,
                    method=CorrelationMethodEnum.PARTIAL_REFERENCE_HEURISTIC,
                    identifier_scope=IdentifierScopeEnum.INSTITUTION_LOCAL,
                    reason_code=CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT,
                    reason_desc=(
                        f"Reference '{reference}' links parties {parties} but global identifiers "
                        "conflict between linked events; contradiction must be preserved."
                    ),
                    events=cluster,
                    identifying_keys=keys,
                    contradictions=contradictions,
                    tolerance_seconds=tolerance_seconds,
                )
            )
        elif len(parties) == 1:
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.CANDIDATE,
                    method=CorrelationMethodEnum.PARTIAL_REFERENCE_HEURISTIC,
                    identifier_scope=IdentifierScopeEnum.INSTITUTION_LOCAL,
                    reason_code=CorrelationReasonCode.CANDIDATE_PARTIAL_REFERENCE_ONLY,
                    reason_desc=(
                        f"Reference '{reference}' is reused within institution '{parties[0]}'. "
                        "Institution-local uniqueness cannot be established, so this remains a candidate."
                    ),
                    events=cluster,
                    identifying_keys={"reference": reference, "party": parties[0]},
                    tolerance_seconds=tolerance_seconds,
                )
            )
        elif len(currencies) > 1:
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.AMBIGUOUS,
                    method=CorrelationMethodEnum.PARTIAL_REFERENCE_HEURISTIC,
                    identifier_scope=IdentifierScopeEnum.INSTITUTION_LOCAL,
                    reason_code=CorrelationReasonCode.CURRENCY_MISMATCH,
                    reason_desc=(
                        f"Cross-institution reference '{reference}' matches but carries contradictory "
                        f"currencies: {currencies}."
                    ),
                    events=cluster,
                    identifying_keys={"reference": reference, "currencies": currencies},
                    contradictions=[f"Currencies differ across reference-linked events: {currencies}"],
                    tolerance_seconds=tolerance_seconds,
                )
            )
        elif len(amounts) == 1 and len(cluster) == 2:
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.CONFIRMED,
                    method=CorrelationMethodEnum.INSTITUTION_SCOPED_REFERENCE,
                    identifier_scope=IdentifierScopeEnum.INSTITUTION_LOCAL,
                    reason_code=CorrelationReasonCode.SECONDARY_INSTITUTION_REFERENCE_VERIFIED,
                    reason_desc=(
                        f"Cross-source reference '{reference}' uniquely links {len(cluster)} parties "
                        f"{parties} with verified currency ({currencies[0]}) and amount."
                    ),
                    events=cluster,
                    identifying_keys={"reference": reference, "parties": parties},
                    tolerance_seconds=tolerance_seconds,
                )
            )
        else:
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.CANDIDATE,
                    method=CorrelationMethodEnum.PARTIAL_REFERENCE_HEURISTIC,
                    identifier_scope=IdentifierScopeEnum.INSTITUTION_LOCAL,
                    reason_code=CorrelationReasonCode.AMBIGUOUS_MULTIPLE_CANDIDATE_COLLISION,
                    reason_desc=(
                        f"Multiple records collide on reference '{reference}' across parties {parties}; "
                        "uniqueness is not guaranteed so this remains a candidate."
                    ),
                    events=cluster,
                    identifying_keys={"reference": reference, "parties": parties},
                    tolerance_seconds=tolerance_seconds,
                )
            )

        for event in cluster:
            assigned_event_ids.add(event.id)

    # =========================================================================
    # PASS 3: Candidate match on temporal + amount proximity (NEVER confirmed)
    # =========================================================================
    remaining = [e for e in sorted_all_events if e.id not in assigned_event_ids]
    consumed_in_proximity: Set[UUID] = set()

    for i, anchor in enumerate(remaining):
        if anchor.id in consumed_in_proximity:
            continue
        cluster = [anchor]
        for other in remaining[i + 1:]:
            if other.id in consumed_in_proximity:
                continue
            if anchor.currency != other.currency:
                continue
            if anchor.amount_minor_units != other.amount_minor_units:
                continue
            delta = abs((anchor.event_timestamp - other.event_timestamp).total_seconds())
            if delta <= tolerance_seconds:
                cluster.append(other)

        if len(cluster) > 1:
            for member in cluster:
                consumed_in_proximity.add(member.id)
                assigned_event_ids.add(member.id)

            observed_session_ids = sorted(
                {s for s in (_clean(e.session_id) for e in cluster) if s}
            )
            groups.append(
                build_group(
                    case_id=resolved_case_id,
                    status=CorrelationStatusEnum.CANDIDATE,
                    method=CorrelationMethodEnum.TEMPORAL_AMOUNT_PROXIMITY,
                    identifier_scope=IdentifierScopeEnum.NONE,
                    reason_code=CorrelationReasonCode.CANDIDATE_TEMPORAL_AMOUNT_PROXIMITY,
                    reason_desc=(
                        f"Events align within {tolerance_seconds}s skew tolerance with identical amount "
                        f"({anchor.amount_minor_units} {anchor.currency}) but lack verified unique "
                        "identifiers. Amount/time similarity alone never confirms a match."
                    ),
                    events=cluster,
                    identifying_keys={
                        "amount_minor_units": anchor.amount_minor_units,
                        "currency": anchor.currency,
                        "parties": sorted({e.party for e in cluster}),
                        "observed_session_ids": observed_session_ids,
                    },
                    tolerance_seconds=tolerance_seconds,
                )
            )

    # =========================================================================
    # PASS 4: Unmatched events
    # =========================================================================
    for event in sorted_all_events:
        if event.id in assigned_event_ids:
            continue
        groups.append(
            build_group(
                case_id=resolved_case_id,
                status=CorrelationStatusEnum.UNMATCHED,
                method=CorrelationMethodEnum.NONE,
                identifier_scope=IdentifierScopeEnum.NONE,
                reason_code=CorrelationReasonCode.INSUFFICIENT_IDENTIFIERS,
                reason_desc=(
                    f"Standalone event from '{event.party}' with insufficient corroborating identifiers "
                    "or material conflict."
                ),
                events=[event],
                identifying_keys={"event_id": str(event.id), "party": event.party},
                tolerance_seconds=tolerance_seconds,
            )
        )
        assigned_event_ids.add(event.id)

    # =========================================================================
    # Deterministic final ordering of output groups.
    # =========================================================================
    status_order = {
        CorrelationStatusEnum.CONFIRMED: 0,
        CorrelationStatusEnum.AMBIGUOUS: 1,
        CorrelationStatusEnum.CANDIDATE: 2,
        CorrelationStatusEnum.UNMATCHED: 3,
    }

    def group_sort_key(group: CorrelationGroup) -> Tuple[int, datetime, str]:
        earliest_timestamp = (
            group.events[0].event_timestamp
            if group.events
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        return (status_order[group.status], earliest_timestamp, group.group_id)

    sorted_groups = sorted(groups, key=group_sort_key)

    summary = {
        "CONFIRMED": sum(1 for g in sorted_groups if g.status == CorrelationStatusEnum.CONFIRMED),
        "CANDIDATE": sum(1 for g in sorted_groups if g.status == CorrelationStatusEnum.CANDIDATE),
        "AMBIGUOUS": sum(1 for g in sorted_groups if g.status == CorrelationStatusEnum.AMBIGUOUS),
        "UNMATCHED": sum(1 for g in sorted_groups if g.status == CorrelationStatusEnum.UNMATCHED),
    }

    return CaseCorrelationResult(
        case_id=resolved_case_id,
        algorithm_version=ALGORITHM_VERSION,
        computed_at=datetime.now(timezone.utc),
        total_events=len(sorted_all_events),
        groups=sorted_groups,
        summary=summary,
    )
