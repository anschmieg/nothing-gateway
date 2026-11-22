"""Disable credential cooling only for the fixed self-hosted local provider.

Uses the native management API with a config ETag, preserving all other entries.
No model inference, GPU loading, or cloud quota changes occur here.
"""
import json
from pathlib import Path
import urllib.request
import yaml

config=Path('/home/agent/cliproxy-staging/config/config.yaml')
before=config.read_bytes()
data=yaml.safe_load(before)
providers=data['api-keys']['openai-compatibility']
local=[p for p in providers if p.get('name')=='local-pc']
assert len(local)==1
provider=local[0]
assert provider['base-url']=='http://100.83.43.41:11441/v1'
assert provider['models']==[{'name':'heretic','max-context-length':94208}]
assert provider.get('request-retry')==0
key=Path('/home/agent/cliproxy-staging/secrets/management.key').read_text().strip()
base='http://127.0.0.1:8080/api/http/services/https-0-iog8gk8g8o0s44840g4o4sso@docker'
with urllib.request.urlopen(base,timeout=5) as r:origin=json.load(r)['loadBalancer']['servers'][0]['url']
url=origin+'/v8/management/config/api-keys/openai-compatibility'
headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=5) as r:
    etag=r.headers['ETag']
assert config.read_bytes()==before
backup=config.with_name('config.yaml.before-local-cold-start-20261115')
if not backup.exists():
    backup.write_bytes(before);backup.chmod(0o600)
provider['disable-cooling']=True
headers['If-Match']=etag
request=urllib.request.Request(url,data=json.dumps(providers).encode(),headers=headers,method='PUT')
with urllib.request.urlopen(request,timeout=10) as r:
    result=json.load(r)
    assert result.get('status')=='ok'
print(json.dumps({'updated':'local-pc','disable-cooling':True,'request-retry':0,'other_providers_preserved':True}))
