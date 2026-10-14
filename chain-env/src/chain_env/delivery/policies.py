"""Resolved, immutable per-subscription quality of service."""
from dataclasses import dataclass, asdict
from chain_env.modeling.model import fields


@dataclass(frozen=True)
class SubscriptionPolicy:
    buffer: str = "fifo"
    capacity: int = 1024
    overflow: str = "reject_new"
    ack: str = "none"
    max_retries: int = 0
    ack_timeout_ms: int = 100
    backoff_multiplier: int = 2
    wait_timeout_ms: int = 100
    transport: str = "tcp"

    @classmethod
    def resolve(cls, qos=None, transport="tcp"):
        qos = cls._memory_only(qos or {})
        fields(qos, ("buffer", "capacity", "overflow", "ack", "retry", "wait_timeout_ms"))
        retry = qos.pop("retry", "none")
        if retry != "none":
            fields(retry, ("max_retries", "ack_timeout_ms", "backoff_multiplier"))
            qos.update({"max_retries": 3, "ack_timeout_ms": 100, "backoff_multiplier": 2})
            qos.update(retry)
        if qos.get("buffer") == "latest":
            qos.setdefault("capacity", 1)
            qos.setdefault("overflow", "drop_oldest")
        result = cls(**qos, transport=transport)
        result.validate()
        return result

    @staticmethod
    def _memory_only(values):
        values = dict(values)
        if values.pop("durability", "memory") != "memory":
            raise ValueError("persistence is not supported; delivery queues are RAM-only")
        return values

    @classmethod
    def from_dict(cls, values):
        result = cls(**cls._memory_only(values))
        result.validate()
        return result

    def validate(self):
        result = self
        for name, choices in {"buffer": ("fifo", "latest"), "overflow": ("reject_new", "drop_oldest", "wait"),
                              "ack": ("none", "received"),
                              "transport": ("tcp", "udp")}.items():
            if getattr(result, name) not in choices:
                raise ValueError(f"invalid {name}: {getattr(result, name)}")
        for name in ("capacity", "ack_timeout_ms", "backoff_multiplier", "wait_timeout_ms"):
            if type(getattr(result, name)) is not int or getattr(result, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(result.max_retries) is not int or result.max_retries < 0:
            raise ValueError("max_retries must be nonnegative")
        if result.max_retries and result.ack != "received":
            raise ValueError("retry requires ack=received")
        if result.buffer == "latest" and (result.capacity != 1 or result.overflow != "drop_oldest"):
            raise ValueError("latest requires capacity=1 and drop_oldest")
        if result.transport == "udp" and result.overflow == "wait":
            raise ValueError("UDP cannot wait for capacity")

    def to_dict(self):
        return asdict(self)
