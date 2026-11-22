"""Apply the model-specific effort label at the final local request boundary."""
import argparse
import hashlib
from pathlib import Path

PIN='ccbccf7e234db4a4552de36dec3a9fab1c6a0793665787370d01646735a199eb'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
args=parser.parse_args()
raw=args.source.read_bytes()
if hashlib.sha256(raw).hexdigest()!=PIN:parser.error('source changed')
s=raw.decode()
anchor="            upstream_body = fold_leading_instructions(body) if self.path == '/v1/responses' else body"
assert s.count(anchor)==1
s=s.replace('import jsonschema\n','import jsonschema\nfrom reasoning_compat import normalize_reasoning\n')
s=s.replace(anchor,anchor+'\n            upstream_body = normalize_reasoning(upstream_body)')
args.output.write_text(s)
