"""Print observations to stderr; delivery and the example do not persist messages."""
import json
import sys
from measurement import Measurement


def on_input(ctx, message: Measurement):
    print(json.dumps({"observer": ctx.name, "sequence": message.sequence, "value": message.value}),
          file=sys.stderr, flush=True)
