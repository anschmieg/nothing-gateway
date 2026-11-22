"""Install tested errors without interrupting an admitted generation."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

root=Path('/var/mnt/Data/AI/model-provider')
controller=root/'app/controller';stage=root/'tmp/controller-errors-tests'
sys.path.insert(0,str(controller))
from resource_admission import acquire_gpu
worker=None;lease=None;deadline=time.monotonic()+180
while True:
    try:
        lease=acquire_gpu()
        worker=(root/'state/worker.lock').open('a')
        fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
        break
    except (RuntimeError,BlockingIOError):
        if worker:worker.close();worker=None
        if lease:lease.close();lease=None
        if time.monotonic()>=deadline:raise RuntimeError('No idle deployment window; no files changed')
        time.sleep(.25)
try:
    server=controller/'server.py';helper=controller/'admission_errors.py'
    registry=root/'app/native/profile-registry.json';r=json.loads(registry.read_text())
    old='091b3d583edd566975c6cb3a934c3416e2660db5eefda2aafaed29b9b3351c4b'
    assert hashlib.sha256(server.read_bytes()).hexdigest()==old
    assert r['source_sha256'][str(server)]==old
    for name,digest in r['source_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
    assert hashlib.sha256((stage/'server.py').read_bytes()).hexdigest()=='fd2da78875db79041d0119508636fe17c717fd7863124c3fdcee6e88d2241cd1'
    assert not helper.exists()
    for p in (server,registry):
        backup=p.with_name(p.name+'.before-admission-errors-20261121');assert not backup.exists();shutil.copy2(p,backup)
    def replace(p,raw):
        tmp=p.with_name(p.name+'.admission-new');tmp.write_bytes(raw);tmp.chmod(0o644);os.replace(tmp,p)
    for p in (helper,server):
        raw=(stage/p.name).read_bytes();replace(p,raw)
        r['source_sha256'][str(p)]=hashlib.sha256(raw).hexdigest()
    replace(registry,(json.dumps(r,indent=2)+'\n').encode())
    subprocess.run(['systemctl','--user','restart','heretic-remote.service'],check=True)
    subprocess.run(['systemctl','--user','is-active','--quiet','heretic-remote.service'],check=True)
    print('Installed controller admission errors in an idle window')
finally:
    if worker:worker.close()
    if lease:lease.close()
