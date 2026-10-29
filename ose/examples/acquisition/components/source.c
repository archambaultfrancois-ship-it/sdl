#include "component.h"

static uint64_t counter;

static int on_tick(OseContext *ctx) {
    Measurement message = {0};
    message.sequence = ++counter;
    message.value = (double)counter;
    return publish_measure(ctx, &message);
}

const ComponentCallbacks component_callbacks = { .on_tick = on_tick };
