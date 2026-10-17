from dataclasses import dataclass

@dataclass(frozen=True)
class MessageInfo:
    publication_id: str
    topic: str
    epoch: str
    sequence: int

@dataclass(frozen=True)
class PublishResult:
    publication_id: str
    epoch: str
    sequence: int
    delivery: dict[str, str]

    @property
    def accepted(self):
        return all(v == "accepted" for v in self.delivery.values())
