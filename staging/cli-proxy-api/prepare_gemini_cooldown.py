#!/usr/bin/env python3
"""Apply temporary Gemini cooldowns to the exact archived native .27 source."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PATCHES = Path(__file__).resolve().parent / 'patches/gemini_cooldown'
NEW_FILES = {
    'gemini_recovery.go': 'sdk/cliproxy/auth/gemini_recovery.go',
    'gemini_recovery_test.go': 'sdk/cliproxy/auth/gemini_recovery_test.go',
    'gemini_cooldown_test.go': 'internal/runtime/executor/gemini_cooldown_test.go',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a new directory')
    pins = json.loads((PATCHES / 'pins.json').read_text())
    for name, expected in pins.items():
        if hashlib.sha256((args.source / name).read_bytes()).hexdigest() != expected:
            parser.error('source differs: ' + name)
    shutil.copytree(args.source, args.output, ignore=shutil.ignore_patterns('.git', '*.so', 'cli-proxy-api'))
    subprocess.run(['patch', '-p1', '--batch', '--forward', '-i', str(PATCHES / 'native.patch')], cwd=args.output, check=True)
    for filename, destination in NEW_FILES.items():
        target = args.output / destination
        if target.exists():
            parser.error('unexpected existing file: ' + destination)
        shutil.copyfile(PATCHES / filename, target)
    files = list(pins) + list(NEW_FILES.values())
    manifest = {'inputs': pins, 'outputs': {name: hashlib.sha256((args.output / name).read_bytes()).hexdigest() for name in files}, 'deployed': False}
    (args.output / 'gemini-cooldown-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'prepared': str(args.output), 'deployed': False}))


if __name__ == '__main__':
    main()
