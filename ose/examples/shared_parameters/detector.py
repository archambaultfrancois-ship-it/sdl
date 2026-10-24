"""Two detectors with individual settings and one shared safety block."""
import json
import sys


def on_tick(ctx):
    detection = ctx.parameters_detection()
    security = ctx.parameters_security()
    state = (detection.enabled and security.enabled,
             min(detection.threshold, security.maximum_threshold), detection.hysteresis, detection.window_ms)
    if state != getattr(ctx, '_last_settings', None):
        print(json.dumps({'component': ctx.name, 'enabled': state[0], 'threshold': state[1],
                          'hysteresis': state[2], 'window_ms': state[3], 'time': ctx.now()}), file=sys.stderr)
        ctx._last_settings = state
