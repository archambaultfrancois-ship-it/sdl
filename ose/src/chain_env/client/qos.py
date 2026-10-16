from dataclasses import dataclass, asdict
from chain_env.delivery.policies import SubscriptionPolicy

@dataclass(frozen=True)
class Retry:
    max_retries: int = 3
    ack_timeout_ms: int = 100
    backoff_multiplier: int = 2

@dataclass(frozen=True)
class Fifo:
    capacity: int = 256
    overflow: str = "reject_new"
    ack: str = "none"
    retry: Retry | None = None
    wait_timeout_ms: int = 100

    def policy(self, transport):
        values = asdict(self)
        values["retry"] = asdict(self.retry) if self.retry else "none"
        return SubscriptionPolicy.resolve(values, transport)

@dataclass(frozen=True)
class Latest:
    ack: str = "none"
    retry: Retry | None = None

    def policy(self, transport):
        return SubscriptionPolicy.resolve(dict(buffer="latest", ack=self.ack,
            retry=asdict(self.retry) if self.retry else "none"), transport)
