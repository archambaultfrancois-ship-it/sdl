"""Transport adapters move bytes; delivery policy belongs to delivery.engine."""
from typing import Protocol


class Transport(Protocol):
    async def send(self, header: dict, payload: bytes = b"") -> None: ...
    async def close(self) -> None: ...
