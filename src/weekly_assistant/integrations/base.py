from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IntegrationStatus:
    name: str
    configured: bool
    mode: str
    missing: tuple[str, ...]
    notes: str


class IntegrationAdapter(Protocol):
    name: str

    def status(self) -> IntegrationStatus:
        """Return local configuration status without performing remote side effects."""
