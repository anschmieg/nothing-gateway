"""Map the standard high effort to this model template's equivalent xhigh label."""
import copy


def normalize_reasoning(request):
    result = copy.deepcopy(request)
    if result.get('reasoning_effort') == 'high':
        result['reasoning_effort'] = 'xhigh'
    for field in ('reasoning', 'chat_template_kwargs'):
        value = result.get(field)
        key = 'effort' if field == 'reasoning' else 'reasoning_effort'
        if isinstance(value, dict) and value.get(key) == 'high':
            value[key] = 'xhigh'
    return result
