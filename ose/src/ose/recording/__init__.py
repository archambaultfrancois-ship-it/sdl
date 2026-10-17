"""Optional, model-independent SDL recording services."""

DEFAULT_LABEL = "recorder"


def session_label(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
        raise ValueError("session label must be nonempty text of at most 256 UTF-8 bytes")
    return value.strip()
