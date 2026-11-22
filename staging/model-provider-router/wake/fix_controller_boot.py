"""Keep the enabled controller unit readable before the Data disk is mounted.

Run as the PC user; the service source on Data remains unchanged. Reload/reenable
and start heretic-remote.service after installing this boot-readable unit.
"""
from pathlib import Path
import os

unit=Path.home()/'.config/systemd/user/heretic-remote.service'
source=Path('/var/mnt/Data/AI/model-provider/app/controller/heretic-remote.service')
if unit.is_symlink():
    assert Path(os.readlink(unit))==source
    data=source.read_bytes()
    backup=unit.with_name('heretic-remote.service.before-boot-fix-20261121.symlink-target')
    assert not backup.exists()
    backup.write_text(str(source)+'\n')
    stage=unit.with_name('heretic-remote.service.boot-new')
    stage.write_bytes(data);stage.chmod(0o644);os.replace(stage,unit)
else:
    assert unit.read_bytes()==source.read_bytes(), 'Local unit differs; review before updating'
print('Controller unit is available on the home filesystem at user-manager startup')
