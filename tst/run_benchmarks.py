#!/usr/bin/env python3
"""Compare five SDL2 workloads across runtimes, reporting useful throughput."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

RATE = re.compile(r'^(small|optional_sparse|optional_dense|variable|packed) (encode|decode) ([0-9.]+) msg/s ([0-9.]+) MiB/s \(([0-9]+) bytes/message\)', re.MULTILINE)
META = re.compile(r'^(\w+) metadata: ([0-9]+) description bytes, ([0-9.]+) us prepare', re.MULTILINE)
CASES = ('small', 'optional_sparse', 'optional_dense', 'variable', 'packed')


def main():
   make = shlex.split(sys.argv[1]) if len(sys.argv)>1 else ['make']
   iterations = sys.argv[2] if len(sys.argv)>2 else '200'
   matlab = sys.argv[3] if len(sys.argv)>3 else ('Matlab' if shutil.which('matlab') else 'Octave')
   rows=[];sizes={}
   for label, target in (('C','bench-c'), ('Rust','bench-rust'), ('Python','bench-python'),
         (matlab,'bench-matlab'), ('Java','bench-java')):
      print('Running {} benchmark...'.format(label), flush=True)
      process=subprocess.run(make+['--no-print-directory','BENCH_ITERATIONS='+iterations,target],
         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
      if process.returncode:
         print(process.stdout);return process.returncode
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
   print('\nSDL2 median throughput; MiB/s counts useful values, excludes wire metadata')
   print('{:<8} {:<16} {:>5} {:>11} {:>10} {:>11} {:>10}'.format('Backend','Case','Bytes','Encode msg/s','MiB/s','Decode msg/s','MiB/s'))
   for row in rows:
      print('{backend:<8} {case:<16} {bytes:>5} {encode_msg_s:>11.1f} {encode_mib_s:>10.4f} {decode_msg_s:>11.1f} {decode_mib_s:>10.4f}'.format(**row))
   os.makedirs('build',exist_ok=True)
   with open('build/benchmarks.json','w') as output:json.dump(rows,output,indent=2);output.write('\n')
   print('Catalogue size and preparation times: build/benchmarks.json')
   return 0


if __name__=='__main__':sys.exit(main())
