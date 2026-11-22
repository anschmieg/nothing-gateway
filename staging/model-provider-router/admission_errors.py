"""Public local-admission errors; private startup diagnostics stay in control status."""
import math


def classify(*, busy, running, action, resource_blocked, profile_conflict, failed, retry_seconds):
    if busy:
        return 429, 'rate_limit_error', 'concurrency_limit_exceeded', 'Local model is busy processing another request. No generation slot became available within the queue wait limit; retry shortly.', 15
    if running:
        if action == 'start':
            return 503, 'service_unavailable', 'resource_not_ready', 'Local model is loading. Startup continues in the background; retry shortly.', 15
        return 503, 'service_unavailable', 'resource_not_ready', 'Local model is stopping or changing profiles. Retry after the transition finishes.', 15
    if resource_blocked:
        return 409, 'conflict_error', 'resource_busy', 'Local inference is blocked by other GPU work or the desktop resource policy. Free the required resources before retrying.', None
    if profile_conflict:
        return 409, 'conflict_error', 'resource_conflict', 'The local model profile conflicts with an active desktop session. Use a compatible profile before retrying.', None
    if failed:
        return 502, 'api_error', 'initialization_failed', 'Local model startup failed. Inspect the local controller status and service logs for the cause.', max(1, math.ceil(retry_seconds)) if retry_seconds > 0 else None
    return 503, 'service_unavailable', 'resource_unavailable', 'Local model is unavailable. Retry shortly; inspect local controller status if this persists.', 15


def public_error_metadata(status, message):
    """Recover metadata dropped by the native Messages error-envelope conversion."""
    base=dict(busy=False,running=False,action=None,resource_blocked=False,
              profile_conflict=False,failed=False,retry_seconds=60)
    states=[{}, {'busy':True}, {'running':True,'action':'start'},
            {'running':True,'action':'stop'}, {'resource_blocked':True},
            {'profile_conflict':True}, {'failed':True}]
    for state in states:
        candidate=classify(**dict(base,**state))
        if candidate[0]==status and candidate[3]==message:
            return candidate[2],None if candidate[2]=='initialization_failed' else candidate[4]
    return None
