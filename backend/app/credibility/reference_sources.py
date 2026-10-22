"""Internal provider results for bounded automatic reference verification."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.credibility.models import CredibilityBibliographicMetadata


@dataclass(frozen=True)
class ReferenceObservation:
    url: str
    fetch_status: str
    http_status: int | None
    retrieved_at: str
    is_cached: bool
    snapshot_ref: str
    provider: str


@dataclass(frozen=True)
class ReferenceConfirmation:
    metadata: CredibilityBibliographicMetadata
    snapshot_ref: str
    url: str
    reason: str
    provider: str


@dataclass
class ReferenceFallback:
    confirmations: list[ReferenceConfirmation] = field(default_factory=list)
    observations: list[ReferenceObservation] = field(default_factory=list)
    reason_code: str = "target_not_found_in_scope"
    reason: str = "No target confirmed within the bounded official-source search."
    identity_snapshot_ref: str | None = None
