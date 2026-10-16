#include "component.h"
static int on_input(ChainContext *ctx, const Measurement *input) {
    Measurement result = *input;
    result.value += 1.0;
    return publish_value(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
