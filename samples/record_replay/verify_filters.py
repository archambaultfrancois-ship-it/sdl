#!/usr/bin/env python3
"""Verify topic/time selection and replay the demo's two IQ bursts using a wire filter."""

import argparse

from sdl_filter import Filter, Status
from sdl_recording import RecordingReader, replay
from pipeline import BURST_FILTER, on_record


def main():
   parser = argparse.ArgumentParser(description=__doc__)
   parser.add_argument('capture')
   parser.add_argument('output')
   args = parser.parse_args()
   reader = RecordingReader(args.capture)
   reader.validate()
   records = list(reader.iter_records())
   assert [r.sequence for r in records] == list(range(10)), 'expected ten recorded captures'
   origin = records[0].log_time_ns
   selected = list(reader.iter_records(topics='radio/iq',
      start_time_ns=origin + 300000000, end_time_ns=origin + 800000000))
   assert [r.sequence for r in selected] == [3, 4, 5, 6, 7], 'topic/time range must be [start,end)'
   assert not list(reader.iter_records(topics='radio/unknown')), 'unknown topic must select nothing'
   assert not list(reader.iter_records(start_time_ns=origin, end_time_ns=origin)), 'empty time range'

   channel = records[0].channel
   with Filter(channel.catalogue, channel.root_type, BURST_FILTER) as predicate:
      bursts = []
      for record in records:
         result = predicate.evaluate_wire(record.payload)
         expected = Status.MATCH if record.sequence in (3, 8) else Status.NO_MATCH
         assert result.status == expected, (record.sequence, result, expected.name)
         if result.matched:
            bursts.append(record)
      assert [r.sequence for r in bursts] == [3, 8], 'wire filter must select only the two bursts'
      with open(args.output, 'w', encoding='utf-8') as output:
         replay(bursts, lambda record: on_record(record, output=output), speed=0)
   print('Topic/time filters verified; IQ wire filter selected sequences 3 and 8 (2/10 captures).')


if __name__ == '__main__':
   main()
