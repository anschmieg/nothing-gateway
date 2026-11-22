"""Update only the integrity pins for the tested reasoning compatibility patch."""
import hashlib
import json
import os
from pathlib import Path
import shutil

base=Path('/var/mnt/Data/AI/model-provider')
p=base/'app/native/profile-registry.json'
server=base/'app/gateway/server.py'
helper=base/'app/gateway/reasoning_compat.py'
original=p.read_bytes();registry=json.loads(original)
old='ccbccf7e234db4a4552de36dec3a9fab1c6a0793665787370d01646735a199eb'
new='6c626b1bce22c3064e417ae68abede0aa58096433dc63edbf019122be939cafb'
helper_hash='0752b052f9fcc0d01a3113a012b1a452f74d8ad42aa83b69f5acf907dbe8dfae'
assert registry['source_sha256'][str(server)]==old
assert not server.is_symlink() and hashlib.sha256(server.read_bytes()).hexdigest()==new
assert not helper.is_symlink() and hashlib.sha256(helper.read_bytes()).hexdigest()==helper_hash
for name,digest in registry['source_sha256'].items():
    if name!=str(server):assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
for profile in registry['profiles'].values():
    s=Path(profile['model_path']).stat()
    assert dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)==profile['model_stat']
backup=p.with_name('profile-registry.json.before-messages-reasoning-20261115')
assert not backup.exists()
shutil.copy2(p,backup)
registry['source_sha256'][str(server)]=new
registry['source_sha256'][str(helper)]=helper_hash
assert p.read_bytes()==original
stage=p.with_name(p.name+'.messages-new');stage.write_text(json.dumps(registry,indent=2)+'\n');stage.chmod(p.stat().st_mode & 0o777);os.replace(stage,p)
print(json.dumps({'updated_source_pins':[str(server),str(helper)],'all_other_source_and_model_pins_verified':True}))
