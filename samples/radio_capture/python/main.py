#!/usr/bin/env python3
"""Radio capture encoder and decoder example."""

import pathlib
import sys
import math

from radio_capture import CaptureMetadata, CaptureMode, RadioCapture
from sdl_runtime import Complex32, decode, decode_dynamic, display, encode


def example_capture():
   return RadioCapture(
      metadata=CaptureMetadata(
         receiver_id='north-ridge-rx-02',
         sequence=8472,
         captured_at_ns=1780000000123456789,
         center_frequency_hz=915000000.0,
         sample_rate_hz=2400000.0,
         mode=CaptureMode.LIVE,
         dc_offset=[0.001, -0.002],
      ),
      operator_note='interference burst near channel edge',
      iq_samples=[
         Complex32(1.0, 0.25),
         Complex32(-0.5, 0.75),
         Complex32(0.125, -1.0),
      ],
      trigger_offsets=[128, 4096, 12000],
   )


def check_capture(wire, indent_width=3, show_display=False):
   decoded = decode(wire, RadioCapture)
   generic = decode_dynamic(wire)
   assert decoded.metadata.receiver_id == 'north-ridge-rx-02'
   assert decoded.metadata.sequence == 8472
   assert decoded.metadata.mode == CaptureMode.LIVE
   assert math.isclose(decoded.metadata.dc_offset[0], 0.001, abs_tol=1e-7)
   assert math.isclose(decoded.metadata.dc_offset[1], -0.002, abs_tol=1e-7)
   assert decoded.operator_note == 'interference burst near channel edge'
   assert len(decoded.iq_samples) == 3
   assert decoded.iq_samples[1] == Complex32(-0.5, 0.75)
   assert decoded.trigger_offsets == [128, 4096, 12000]
   assert generic.type_name == 'RadioCapture'
   assert len(generic.fields['iq_samples']) == 3
   print('Python decoded {} I/Q samples and 3 trigger offsets ({} wire bytes)'.format(
      len(decoded.iq_samples), len(wire)))
   if show_display:
      print(display(decoded, indent_width), end='')


def main(args):
   mode = args[1] if len(args) > 1 else 'roundtrip'
   indent_width = int(args[2]) if len(args) > 2 and mode != 'decode' and mode != 'encode' else 3
   if mode == 'display':
      print(display(example_capture(), indent_width), end='')
      return
   if mode == 'decode':
      check_capture(pathlib.Path(args[2]).read_bytes())
      return
   wire = encode(example_capture())
   if mode == 'encode':
      pathlib.Path(args[2]).write_bytes(wire)
      print('Python wrote {} wire bytes to {}'.format(len(wire), args[2]))
      return
   check_capture(wire, indent_width, show_display=True)


if __name__ == '__main__':
   main(sys.argv)
