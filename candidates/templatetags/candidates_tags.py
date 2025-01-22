from django import template
from django.template.loader import render_to_string

register = template.Library()

@register.simple_tag
def aladin_finderchart(candidate):
    """
    Renders the Aladin finder chart for a given candidate.
    """
    context = {'candidate': candidate}
    return render_to_string('partials/aladin_finderchart.html', context)