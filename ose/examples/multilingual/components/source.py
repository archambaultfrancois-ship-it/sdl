from measurement import Measurement


def on_tick(ctx):
    ctx.publish_value(Measurement(sequence=ctx.tick_index, value=ctx.now()))
