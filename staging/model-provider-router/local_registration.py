"""Exact native registration for the existing, unmetered local controller."""
import hashlib
from datetime import datetime, timedelta, timezone


def local_allowance(key, base):
    kind='openai-compatibility:local-pc'
    auth=kind+':'+hashlib.sha256('\0'.join((kind,key.strip(),base.strip(),'')).encode()).hexdigest()[:12]
    return {'provider':'openai-compatible-local-pc','auth-id':auth,
            'source-models':['heretic','local/qwen3.8-heretic'],
            'billing-class':'FREE','no-paid-overage':True,
            'basis':'Fixed self-hosted local controller and explicit local-only model; no metered cloud route',
            'verified-until':(datetime.now(timezone.utc)+timedelta(days=365)).isoformat().replace('+00:00','Z')}
