"""Make verified profile selection independent of reboot-assigned disk numbers."""
import argparse
import hashlib
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
args=parser.parse_args()
raw=args.source.read_bytes()
if hashlib.sha256(raw).hexdigest()!='eb49b9ce045c2b9102a9900fcd70737bb9d8e610f5e806191082ec21db4fb9a1':
    parser.error('Profile selector source differs from the reviewed version')
s=raw.decode()
anchor="        if actual!=item['model_stat']:raise RuntimeError('Verified model artifact changed; revalidate before switching')"
assert s.count(anchor)==1
replacement="""        # guard() verifies the Data filesystem UUID before verify(). Linux device
        # numbers may change at reboot; retain all stable file identity checks.
        expected={k:v for k,v in item['model_stat'].items() if k!='device'}
        actual.pop('device')
        if actual!=expected:raise RuntimeError('Verified model artifact changed; revalidate before switching')"""
args.output.write_text(s.replace(anchor,replacement))
