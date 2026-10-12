#!/usr/bin/env python3
"""Record synthetic IQ captures while delivering the same payloads to analysis."""

import argparse
import math
import time

from radio_capture import CaptureMetadata, CaptureMode, RadioCapture
from sdl_runtime import Complex32, description, encode, prepare
from sdl_recording import RecordingRecord, RecordingWriter
from pipeline import on_record


def acquire_captures(count):
   origin = time.time_ns()
   for sequence in range(count):
      samples = []
      for i in range(4096):
         amplitude = 3.0 if sequence % 5 == 3 and 1024 <= i < 1088 else 0.5
         phase = 2 * math.pi * i / 32
         samples.append(Complex32(amplitude * math.cos(phase), amplitude * math.sin(phase)))
      yield RadioCapture(metadata=CaptureMetadata(receiver_id='demo-rx-01',
         sequence=sequence, captured_at_ns=origin + sequence * 100000000,
         center_frequency_hz=915000000, sample_rate_hz=2400000,
         mode=CaptureMode.LIVE, dc_offset=[0, 0]),
         operator_note='synthetic interference' if sequence % 5 == 3 else None,
         iq_samples=samples, trigger_offsets=[1024] if sequence % 5 == 3 else [])


def main():
   parser = argparse.ArgumentParser(description=__doc__)
   parser.add_argument('output')
   parser.add_argument('--count', type=int, default=10)
   parser.add_argument('--overwrite', action='store_true')
   args = parser.parse_args()
   if args.count < 1:
      parser.error('--count must be positive')
   catalogue = description(RadioCapture)
   context = prepare(catalogue, [RadioCapture])
   with RecordingWriter(args.output, overwrite=args.overwrite,
         metadata={'producer': 'SDL record/replay demo', 'receiver_id': 'demo-rx-01'}) as writer:
      channel = writer.register_channel(topic='radio/iq', catalogue=catalogue, root_type='RadioCapture')
      for capture in acquire_captures(args.count):
         payload = encode(context, capture)
         timestamp = capture.metadata.captured_at_ns
         writer.write_encoded(channel, payload, log_time_ns=timestamp,
            publish_time_ns=timestamp, sequence=capture.metadata.sequence)
         on_record(RecordingRecord(channel, payload, timestamp, timestamp,
            capture.metadata.sequence))


if __name__ == '__main__':
   main()
