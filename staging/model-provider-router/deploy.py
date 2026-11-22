"""Install the tested local Messages route on the existing gateway host.

Run as root on that host with --candidate pointing to the tested router.py.
Registers only the fixed local alias, reusing the existing controller key.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import yaml
from local_registration import local_allowance

ORIGINAL_SHA = 'aa59f53eb56cac1842b19fedd74b497643df5ef06737702f1f2b16ce17d1cede'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args()
    config=Path('/home/agent/cliproxy-staging/config/config.yaml')
    router=Path('/opt/model-provider-router/router.py')
    before=config.read_bytes(); router_before=router.read_bytes()
    if hashlib.sha256(router_before).hexdigest()!=ORIGINAL_SHA:
        parser.error('router source changed; review required')
    data=yaml.safe_load(before)
    providers=data['api-keys'].setdefault('openai-compatibility',[])
    if any(p.get('name')=='local-pc' or any(m.get('alias')=='local/qwen3.8-heretic' for m in p.get('models',[])) for p in providers):
        parser.error('local registration already exists')
    key=Path('/etc/model-provider-router/pc-api-key').read_text().strip()
    providers.append({'name':'local-pc','base-url':'http://100.83.43.41:11441/v1','request-retry':0,'disable-cooling':True,'disable-redirects':True,'support-prompt-cache-key':False,'keys':[{'api-key':key}],'models':[{'name':'heretic','max-context-length':94208}]})
    data['models'].setdefault('canonical-routing',{}).setdefault('no-charge-allowances',[]).append(local_allowance(key,'http://100.83.43.41:11441/v1'))
    with urllib.request.urlopen('http://127.0.0.1:8080/api/http/services/https-0-iog8gk8g8o0s44840g4o4sso@docker',timeout=5) as response:
        service=json.load(response)
    assert service['status']=='enabled' and service['provider']=='docker'
    servers=service['loadBalancer']['servers'];assert len(servers)==1
    origin=servers[0]['url'].rstrip('/')
    backup=Path('/home/agent/cliproxy-staging/local-messages-backup-20261115')
    backup.mkdir(mode=0o700,exist_ok=True)
    (backup/'config.yaml').write_bytes(before);(backup/'config.yaml').chmod(0o600)
    (backup/'router.py').write_bytes(router_before)
    def atomic(path,raw):
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw)
            os.chmod(tmp,path.stat().st_mode & 0o777)
            os.chown(tmp,path.stat().st_uid,path.stat().st_gid)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    try:
        if config.read_bytes()!=before:raise RuntimeError('configuration changed concurrently')
        atomic(config,yaml.safe_dump(data,sort_keys=False).encode())
        # The running host watches the config inode; atomic replacement needs
        # a process restart to attach its watch and register new providers.
        subprocess.run(['docker','restart','--time','60','iog8gk8g8o0s44840g4o4sso-20261106T175924'],check=True,stdout=subprocess.DEVNULL)
        client_key=data['access']['api-keys'][0]
        for _ in range(100):
            request=urllib.request.Request(origin+'/v1/models',headers={'Authorization':'Bearer '+client_key})
            try:
                with urllib.request.urlopen(request,timeout=5) as response:catalog=json.load(response)
            except (urllib.error.URLError,TimeoutError):
                time.sleep(.2)
                continue
            if any(m.get('owned_by')=='local-pc' and (m.get('id')=='local/qwen3.8-heretic' or 'local-pc/heretic' in m.get('overrides',[])) for m in catalog.get('data',[])):break
            time.sleep(.2)
        else:raise RuntimeError('native local alias not published')
        atomic(router,args.candidate.read_bytes())
        subprocess.run(['systemctl','restart','model-provider-router'],check=True)
        subprocess.run(['systemctl','is-active','--quiet','model-provider-router'],check=True)
    except BaseException:
        atomic(config,before);atomic(router,router_before)
        subprocess.run(['docker','restart','--time','60','iog8gk8g8o0s44840g4o4sso-20261106T175924'],check=False,stdout=subprocess.DEVNULL)
        subprocess.run(['systemctl','restart','model-provider-router'],check=False)
        raise
    print(json.dumps({'deployed':True,'native_alias':'local/qwen3.8-heretic','backup':str(backup),'provider_inference_requests':0}))


if __name__=='__main__':
    with open('/run/lock/model-provider-translation.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        main()
