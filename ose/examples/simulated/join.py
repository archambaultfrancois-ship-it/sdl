"""Join the two branches, optionally consuming a scripted external offset."""
from measurement import Measurement


def on_tick(ctx, inputs):
    left, right, offset = inputs["left"], inputs["right"], inputs["offset"]
    assert left.sequence == right.sequence
    ctx.publish_result(Measurement(sequence=left.sequence,
        value=left.value + right.value + (offset.value if offset is not None else 0.0)))
