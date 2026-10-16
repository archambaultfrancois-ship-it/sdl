"""Prepared predicates: median of five warm trials, codec allocations included."""
import statistics
import time

from telemetry.model import Sample, Vector
from sdl_runtime import decode, description, encode, prepare
from sdl_filter import Filter


def measure(action, iterations):
    for _ in range(50):
        action()
    trials = []
    for _ in range(5):
        start = time.perf_counter_ns()
        for _ in range(iterations):
            action()
        trials.append((time.perf_counter_ns() - start) / iterations)
    return statistics.median(trials)


def bench(value, catalogue, label, expression, iterations):
    context = prepare(catalogue, type(value))
    wire = encode(context, value)
    with Filter(catalogue, value._SDL_NAME, expression) as predicate:
        assert predicate.evaluate(value).matched
        assert predicate.evaluate_wire(wire).matched
        actions = (lambda: encode(context, value), lambda: decode(context, wire),
                   lambda: predicate.evaluate(value), lambda: predicate.evaluate_wire(wire))
        timings = [measure(action, iterations) for action in actions]
    print('BENCH Python', label, *('%.1f' % t for t in timings), flush=True)


v = Vector(x=3., y=4.)
c = 'SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}'
bench(v, c, 'scalar', 'x>0', 10000)
bench(v, c, 'arithmetic', 'x*x+y*y<=25', 10000)
v = Sample(temperature=12.34, position=v, points=[Vector(x=3., y=4.) for _ in range(256)])
bench(v, description(Sample), 'collection256', 'all(points,p=>p.x>=0&&p.y>=0)', 300)
