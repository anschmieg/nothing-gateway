"""Install generic error-code names in the shared controller error catalog."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

renames={'local_resources_busy':'resource_busy','model_busy':'concurrency_limit_exceeded',
         'model_loading':'resource_not_ready','model_transitioning':'resource_not_ready',
         'model_profile_conflict':'resource_conflict','model_start_failed':'initialization_failed',
         'model_unavailable':'resource_unavailable'}
root=Path('/var/mnt/Data/AI/model-provider');controller=root/'app/controller'
sys.path.insert(0,str(controller))
from resource_admission import acquire_gpu
lease=worker=None;deadline=time.monotonic()+180
while True:
    try:
        lease=acquire_gpu();worker=(root/'state/worker.lock').open('a')
        fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB);break
    except (RuntimeError,BlockingIOError):
        if worker:worker.close();worker=None
        if lease:lease.close();lease=None
        if time.monotonic()>=deadline:raise RuntimeError('No idle window; nothing changed')
        time.sleep(.25)
try:
    p=controller/'admission_errors.py';registry=root/'app/native/profile-registry.json'
    original=p.read_text();expected=original
    for old,new in renames.items():expected=expected.replace(old,new)
    candidate=(root/'tmp/controller-errors-tests/admission_errors.py').read_text()
    assert expected==candidate and expected!=original
    data=json.loads(registry.read_text())
    for name,digest in data['source_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
    for file in (p,registry):
        backup=file.with_name(file.name+'.before-generic-code-names-20261121');assert not backup.exists();shutil.copy2(file,backup)
    def replace(path,raw):
        stage=path.with_name(path.name+'.codes-new');stage.write_bytes(raw);stage.chmod(path.stat().st_mode & 0o777);os.replace(stage,path)
    replace(p,candidate.encode());data['source_sha256'][str(p)]=hashlib.sha256(candidate.encode()).hexdigest()
    replace(registry,(json.dumps(data,indent=2)+'\n').encode())
    subprocess.run(['systemctl','--user','restart','heretic-remote.service'],check=True)
    subprocess.run(['systemctl','--user','is-active','--quiet','heretic-remote.service'],check=True)
    print('Installed generic admission codes in an idle window')
finally:
    if worker:worker.close()
    if lease:lease.close()
