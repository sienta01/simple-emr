from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Current query string with some parameters replaced (None removes one)."""
    params = context['request'].GET.copy()
    for transient in ('partial', 'page'):
        params.pop(transient, None)
    for key, value in changes.items():
        if value is None or value == '':
            params.pop(key, None)
        else:
            params[key] = str(value)
    encoded = params.urlencode()
    return f'?{encoded}' if encoded else '?'
