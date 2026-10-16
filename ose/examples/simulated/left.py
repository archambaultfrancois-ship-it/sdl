"""One independent branch of the parallel simulation example."""
from measurement import Measurement


def on_input(ctx, message: Measurement):
    ctx.publish_result(Measurement(sequence=message.sequence,
                                   value=message.value + 1.0 + ctx.now()))
