from measurement import Measurement


def on_tick(ctx, inputs):
    assert len({value.sequence for value in inputs.values()}) == 1
    ctx.publish_value(Measurement(sequence=ctx.tick_index,
                                  value=sum(value.value for value in inputs.values())))
