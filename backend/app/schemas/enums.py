"""TRACE Domain Enums.

These enums represent strictly frozen domain classifications.
The five permitted outcomes and four strength labels MUST NOT be altered,
expanded, or mapped to probabilistic/numeric confidence percentages.
"""

from enum import Enum


class OutcomeEnum(str, Enum):
    """The 5 exact permitted outcome classifications for deterministic analysis."""

    DISCREPANCY_CONFIRMED = "DISCREPANCY_CONFIRMED"
    LIKELY_ROOT_CAUSE = "LIKELY_ROOT_CAUSE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    NO_DISCREPANCY_FOUND = "NO_DISCREPANCY_FOUND"


class StrengthEnum(str, Enum):
    """The 4 exact permitted qualitative strength labels.

    Numerical confidence percentages are strictly prohibited.
    """

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class InstitutionPartyEnum(str, Enum):
    """Institutions involved in transaction lifecycle."""

    ORIGINATING_BANK = "ORIGINATING_BANK"
    SWITCH = "SWITCH"
    RECEIVING_BANK = "RECEIVING_BANK"
    MERCHANT = "MERCHANT"


class EventActionEnum(str, Enum):
    """Normalized canonical event actions."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"
    ACK = "ACK"
    REVERSAL_ATTEMPT = "REVERSAL_ATTEMPT"
    TIMEOUT = "TIMEOUT"


class EventStatusEnum(str, Enum):
    """Normalized canonical event execution status."""

    SUCCESS = "SUCCESS"
    PENDING = "PENDING"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class FlagSeverityEnum(str, Enum):
    """Data quality flag severity.

    CRITICAL flags automatically cap finding strength at MODERATE.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL = "CRITICAL"


class CaseStatusEnum(str, Enum):
    """Lifecycle status of a dispute case."""

    OPEN = "OPEN"
    ANALYZED = "ANALYZED"
    REVIEWED = "REVIEWED"
    CLOSED = "CLOSED"


class ReviewVerdictEnum(str, Enum):
    """Investigator review determinations."""

    CONCUR = "CONCUR"
    OVERRULE = "OVERRULE"
    REQUEST_FURTHER_EVIDENCE = "REQUEST_FURTHER_EVIDENCE"


class ReviewDecisionEnum(str, Enum):
    """Human assessment of a machine finding; never a replacement finding."""

    ACCEPT_FINDING = "ACCEPT_FINDING"
    DISPUTE_FINDING = "DISPUTE_FINDING"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
