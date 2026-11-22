"""Apply the same stable file-identity check at native model process startup."""
import argparse
import hashlib
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
args=parser.parse_args();raw=args.source.read_bytes()
if hashlib.sha256(raw).hexdigest()!='de90a711d5253b0479c83fac0f5ddd57e7871a3e191aed22139ad972dc4d0486':parser.error('Runtime source differs')
s=raw.decode()
anchor="  if actual!=cfg['model_stat']:raise RuntimeError('Verified model artifact changed; revalidate before starting')"
assert s.count(anchor)==1
replacement="""  # guard() has verified the filesystem UUID; device numbers can change at boot.
  expected={k:v for k,v in cfg['model_stat'].items() if k!='device'}
  actual.pop('device')
  if actual!=expected:raise RuntimeError('Verified model artifact changed; revalidate before starting')"""
args.output.write_text(s.replace(anchor,replacement))
