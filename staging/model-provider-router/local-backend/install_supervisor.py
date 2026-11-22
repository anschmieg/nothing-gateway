"""Install the prepared Heretic gateway files and update their integrity pins."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

BASE = Path("/var/mnt/Data/AI/model-provider")
GATEWAY = BASE / "app/gateway"
REGISTRY = BASE / "app/native/profile-registry.json"
SERVER = GATEWAY / "server.py"
SUPERVISOR = GATEWAY / "reasoning_supervisor.py"
PREVIOUS_SERVER_SHA = "6c626b1bce22c3064e417ae68abede0aa58096433dc63edbf019122be939cafb"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replace(path, data, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        staged.write(data)
    try:
        os.chmod(staged.name, mode)
        os.replace(staged.name, path)
    finally:
        Path(staged.name).unlink(missing_ok=True)


def install(server, helper):
    with (BASE / "state/worker.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = REGISTRY.read_bytes()
        registry = json.loads(current)
        pins = registry["source_sha256"]
        if pins.get(str(SERVER)) != PREVIOUS_SERVER_SHA or SUPERVISOR.exists():
            raise SystemExit("Unexpected gateway revision or existing supervisor")
        for filename, expected in pins.items():
            path = Path(filename)
            if digest(path.read_bytes()) != expected:
                raise SystemExit(f"Existing integrity pin failed for {path}")
        backup = Path(tempfile.mkdtemp(prefix="reasoning-supervisor-before-", dir=BASE / "state"))
        previous_server = shutil.copy2(SERVER, backup / SERVER.name)
        previous_registry = shutil.copy2(REGISTRY, backup / REGISTRY.name)
        server_mode = SERVER.stat().st_mode & 0o777
        registry_mode = REGISTRY.stat().st_mode & 0o777
        pins[str(SERVER)] = digest(server)
        pins[str(SUPERVISOR)] = digest(helper)
        try:
            replace(SUPERVISOR, helper, 0o644)
            replace(SERVER, server, server_mode)
            replace(REGISTRY, (json.dumps(registry, indent=2) + "\n").encode(), registry_mode)
        except BaseException:
            replace(SERVER, Path(previous_server).read_bytes(), server_mode)
            replace(REGISTRY, Path(previous_registry).read_bytes(), registry_mode)
            SUPERVISOR.unlink(missing_ok=True)
            raise
        return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", type=Path)
    parser.add_argument("helper", type=Path)
    args = parser.parse_args()
    server_data, helper_data = args.server.read_bytes(), args.helper.read_bytes()
    compile(server_data, str(args.server), "exec")
    compile(helper_data, str(args.helper), "exec")
    print(install(server_data, helper_data))
