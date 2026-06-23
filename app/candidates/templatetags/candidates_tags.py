from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from candidates.views import render_candidate_comments_response
from candidates.utils import check_candidate_exists_for_target

register = template.Library()

@register.simple_tag
def aladin_finderchart(candidate):
    """
    Renders the Aladin finder chart for a given candidate.
    """
    context = {'candidate': candidate}
    return render_to_string('partials/aladin_finderchart.html', context)


@register.simple_tag
def candidate_for_target(target):
    """
    Resolves the candidate matched to a target for shared comment threads.
    """
    return check_candidate_exists_for_target(target)


@register.simple_tag(takes_context=True)
def candidate_comments(context, candidate):
    """
    Renders the shared candidate comment section.
    """
    request = context.get("request")
    if request is None:
        return ""
    response = render_candidate_comments_response(request, candidate)
    return mark_safe(response.content.decode("utf-8"))
