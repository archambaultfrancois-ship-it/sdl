#!/usr/bin/env python3
"""Compare six SDL2 workloads across runtimes, reporting useful throughput."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

RATE = re.compile(r'^(small|optional_sparse|optional_dense|variable|packed|packed_struct) (encode|decode) ([0-9.]+) msg/s ([0-9.]+) MiB/s \(([0-9]+) bytes/message\)', re.MULTILINE)
META = re.compile(r'^(\w+) metadata: ([0-9]+) description bytes, ([0-9.]+) us prepare', re.MULTILINE)
CASES = ('small', 'optional_sparse', 'optional_dense', 'variable', 'packed', 'packed_struct')


def print_matrix(rows):
   preferred = ('C', 'Rust', 'Java', 'Python', 'Matlab', 'Octave')
   present = list(dict.fromkeys(row['backend'] for row in rows))
   backends = [name for name in preferred if name in present]
   backends += [name for name in present if name not in preferred]
   measurements = {(row['case'], row['backend']): row for row in rows}
   values = {(case, backend): tuple(
      '{:.0f}'.format(row[op + '_mib_s'] * 1048576 / 1000000)
      for op in ('encode', 'decode'))
      for (case, backend), row in measurements.items() if case in CASES}
   number_widths = {backend: tuple(
      max((len(pair[i]) for (case, name), pair in values.items() if name == backend),
          default=1) for i in range(2)) for backend in backends}
   table = [['Case'] + backends]
   for case in CASES:
      cells = [case]
      for backend in backends:
         pair = values.get((case, backend))
         if pair is None:
            cells.append('—')
            continue
         enc_width, dec_width = number_widths[backend]
         cells.append(pair[0].rjust(enc_width) + ' / ' + pair[1].rjust(dec_width))
      table.append(cells)
   widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
   def line(cells):
      return '| ' + ' | '.join(
         cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
         for i, cell in enumerate(cells)) + ' |'
   print('\nSDL2 median throughput — MB/s (Enc / Dec), rounded to integers')
   print(line(table[0]))
   print('| ' + ' | '.join('-' * widths[0] if i == 0 else '-' * (width - 1) + ':'
      for i, width in enumerate(widths)) + ' |')
   for cells in table[1:]:
      print(line(cells))
   print('MB/s = 1,000,000 useful bytes/s; excludes wire metadata. 0 means < 0.5 MB/s.')


def main():
   make = shlex.split(sys.argv[1]) if len(sys.argv)>1 else ['make']
   iterations = sys.argv[2] if len(sys.argv)>2 else '200'
   matlab = sys.argv[3] if len(sys.argv)>3 else ('Matlab' if shutil.which('matlab') else 'Octave')
   rows=[];sizes={};buffer_table=None
   for label, target in (('C','bench-c'), ('Rust','bench-rust'), ('Python','bench-python'),
         (matlab,'bench-matlab'), ('Java','bench-java')):
      print('Running {} benchmark...'.format(label), flush=True)
      process=subprocess.run(make+['--no-print-directory','BENCH_ITERATIONS='+iterations,target],
         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
      if process.returncode:
         print(process.stdout);return process.returncode
      marker='Python Packed buffer API — MB/s, rounded to integers'
      if label=='Python' and marker in process.stdout:
         buffer_table=marker+process.stdout.split(marker,1)[1]
      rates={(case,op):(float(msg),float(mib),int(size)) for case,op,msg,mib,size in RATE.findall(process.stdout)}
      meta={case:(int(size),float(us)) for case,size,us in META.findall(process.stdout)}
      for case in CASES:
         if (case,'encode') not in rates or (case,'decode') not in rates or case not in meta:
            print(process.stdout);raise RuntimeError('missing benchmark case '+case)
         encode,decode=rates[case,'encode'],rates[case,'decode']
         if encode[2]!=decode[2] or (case in sizes and sizes[case]!=encode[2]):
            raise RuntimeError('wire sizes differ across backends: '+case)
         sizes[case]=encode[2]
         rows.append(dict(backend=label,case=case,bytes=encode[2],description_bytes=meta[case][0],
            prepare_us=meta[case][1],encode_msg_s=encode[0],encode_mib_s=encode[1],decode_msg_s=decode[0],decode_mib_s=decode[1]))
   print_matrix(rows)
   if buffer_table is not None:
      print('\n'+buffer_table.rstrip())
   os.makedirs('build',exist_ok=True)
   with open('build/benchmarks.json','w') as output:json.dump(rows,output,indent=2);output.write('\n')
   print('Catalogue size and preparation times: build/benchmarks.json')
   if buffer_table is not None:
      print('Python buffer measurements: build/python-buffer-benchmarks.json')
   return 0


if __name__=='__main__':sys.exit(main())
