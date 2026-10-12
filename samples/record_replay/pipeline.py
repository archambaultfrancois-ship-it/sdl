"""The same analysis entry point for live captures and recorded SDL2 payloads."""

from sdl_runtime import SdlDynamicMessage, display


def on_record(record):
   capture = record.decode().fields
   metadata = capture['metadata'].fields
   samples = capture['iq_samples']
   powers = [s.real * s.real + s.imag * s.imag for s in samples]
   analysis = SdlDynamicMessage('CaptureAnalysis', {
      'sequence': metadata['sequence'],
      'captured_at_ns': metadata['captured_at_ns'],
      'sample_count': len(samples),
      'peak_power': max(powers, default=0),
      'burst_detected': any(power > 4 for power in powers),
   })
   print(display(analysis), end='')
