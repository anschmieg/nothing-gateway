"""Prepare a reviewed copy of the local controller with specific admission errors."""
import argparse
import hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
raw=a.source.read_bytes()
if hashlib.sha256(raw).hexdigest()!='091b3d583edd566975c6cb3a934c3416e2660db5eefda2aafaed29b9b3351c4b':p.error('Controller source differs from reviewed input')
s=raw.decode()
assert 'from resource_admission import blocked_reason, game_running, acquire_gpu' in s
s=s.replace('import session_policy\n','import session_policy\nfrom admission_errors import classify\n',1)
anchor='\ndef key(name):'
assert s.count(anchor)==1
function='''
def admission_failure():
    # Observe the reason under the same admission lock; do not expose raw job errors.
    with LOCK:
        busy = ACTIVE or worker_busy()
        job = JOB or {}
        running = job.get('status') == 'running'
        reason = None if busy or running else blocked_reason()
        own = ownership() if not busy and not running and not reason else None
        profile_conflict = bool(own and own['mode'] != 'desktop' and desktop_active())
        loaded = ready() if not busy and not running and not reason else False
        result = classify(busy=busy, running=running, action=job.get('action'),
                          resource_blocked=bool(reason), profile_conflict=profile_conflict,
                          failed=not loaded and job.get('status') == 'failed',
                          retry_seconds=FAILURE_COOLDOWN - (time.time() - job.get('at', 0)))
        if loaded and not reason and not profile_conflict:
            # A GPU lease can be held outside the controller's worker lock.
            result = classify(busy=True, running=False, action=None, resource_blocked=False,
                              profile_conflict=False, failed=False, retry_seconds=0)
        status, kind, code, message, retry_after = result
        return status, {'error': {'message': message, 'type': kind, 'code': code}}, retry_after

'''
s=s.replace(anchor,'\n'+function+anchor)
old="""                return self.reply(503, {'error': {
                    'message': 'Local model is not available yet; retry shortly.',
                    'type': 'service_unavailable', 'code': 'model_not_ready'}}, retry_after=15)"""
assert s.count(old)==1
s=s.replace(old,"                status, error, retry_after = admission_failure()\n                return self.reply(status, error, retry_after=retry_after)")
a.output.write_text(s)
