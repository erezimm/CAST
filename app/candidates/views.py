import logging
from collections import defaultdict
from functools import wraps
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from types import SimpleNamespace

import astropy.units as u

from astropy.coordinates import SkyCoord
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models import Subquery, OuterRef
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.contrib.sites.shortcuts import get_current_site
from django_comments.models import Comment
from tom_targets.models import Target

from cast.external_api_status import get_external_api_service_status, is_external_api_service_enabled
from .astro_colibri import prepare_astro_colibri_data, send_astro_colibri
from .forms import FileUploadForm
from .ingestion import process_json_file
from .models import Candidate, CandidateDataProduct, CandidateAlert
from .models.candidate import CLASSIFICATION_CHOICES
from .photometry_utils import generate_photometry_graph, get_atlas_fp, get_ztf_fp
from .services.enrichment import update_candidate_cutouts
from .tns_utils import send_tns_report, tns_report_details, set_reported_by_LAST
from .utils import (
    add_candidate_as_target,
    can_send_tns_report,
    check_target_exists_for_candidate,
    get_horizons_data,
)

logger = logging.getLogger(__name__)


def tns_permission_required(view_func):
    """
    Gate a view behind TNS-report permission.

    Unlike ``user_passes_test``, which redirects an already-authenticated but
    unauthorized user back to the login page (an unhelpful "re-authenticate" loop),
    this shows a clear "you do not have permission" message and returns the user to
    where they came from.
    """
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not can_send_tns_report(request.user):
            messages.error(request, "You do not have permission to send TNS reports.")
            return_url = _safe_return_url(request)
            return redirect(return_url)
        return view_func(request, *args, **kwargs)
    return _wrapped


def _missing_reporter_name_response(request, return_url):
    """
    Ensure the user has a name to attribute the TNS report to.

    TNS reports must name a reporter, which is taken from the user's profile. If the
    first or last name is missing, return a redirect with a clear message instead of
    filing a report with no attribution (or crashing while formatting the name).
    Returns ``None`` when the name is present and the caller may proceed.
    """
    user = request.user
    if not ((user.first_name or '').strip() and (user.last_name or '').strip()):
        messages.error(
            request,
            "Please set your first and last name in your profile before sending a TNS report.",
        )
        return redirect(return_url)
    return None


def _safe_return_url(request, default=None):
    """
    Return the POSTed ``return_url`` only if it points back to this site.

    Guards against open-redirects: an attacker could otherwise POST
    ``return_url=https://evil.example`` and have the view redirect off-site.
    Falls back to the candidate list when the URL is missing or external.
    """
    default = default or reverse('candidates:list')
    url = request.POST.get('return_url')
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return default


def extract_params_from_request(request):
    return {
        'filter_value': request.GET.get('filter', 'all'),
        'start_datetime': request.GET.get('start_datetime'),
        'end_datetime': request.GET.get('end_datetime'),
        'max_distance': request.GET.get('max_distance', ''),
        'fieldid_filter': request.GET.get('fieldid_filter', ''),
        'ToO_filter': request.GET.get('ToO_filter', ''),
        'discovery_date': request.GET.get('discovery_date'),
        'items_per_page': request.GET.get('items_per_page', 25),
        'return_url': request.get_full_path(),
    }


@login_required
def upload_file_view(request):
    """
    Handles file uploads and processes candidates from a JSON file.
    """
    if request.method == 'POST':
        form = FileUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']  # Get the uploaded file

            try:
                # Process the uploaded file directly (no need to save temporarily)
                candidate_count = process_json_file(uploaded_file)
                if candidate_count is None:
                    messages.warning(request, "Candidate already exists.")
                else:
                    messages.success(request, f"Successfully processed {candidate_count} candidates.")
            except Exception as e:
                # Handle any errors during file processing
                messages.error(request, f"Error processing file: {e}")

            # Redirect to the candidate list view
            return redirect('candidates:list')
    else:
        form = FileUploadForm()

    return render(request, 'candidates/upload.html', {'form': form})


@login_required
def delete_candidate_view(request):
    """
    Handles deletion of a candidate via form submission.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')  # Get candidate ID from the form
        candidate = get_object_or_404(Candidate, id=candidate_id)
        return_url = _safe_return_url(request)

        # Delete the candidate
        candidate_name = candidate.name
        candidate.delete()

        # Add a success message
        messages.success(request, f"Candidate '{candidate_name}' has been deleted.")

        if request.htmx:
            response = HttpResponse("")
            response["HX-Trigger"] = "refreshMessages"
            return response

        return redirect(return_url)

    # Redirect back to the candidate list
    return redirect('candidates:list')


@login_required
def refresh_atlas_view(request, candidate_id):
    """
    Query Atlas for photometry for a candidate.
    Does not add photometry that already exists.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    try:
        daysago = request.POST.get('daysago')
        get_atlas_fp(candidate, int(daysago))
        messages.success(request, f"Atlas photometry was updated for {candidate.name}.")
    except Exception as e:
        messages.error(request, f"Failed to refresh Atlas for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        if request.htmx:
            return render_candidate_row_response(request, candidate)
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)


@login_required
def set_reported_by_last_view(request, candidate_id):
    """
    Set the reported_by_LAST field for a candidate.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    try:
        set_reported_by_LAST(candidate_id)
        messages.success(request, f"Candidate {candidate.name} has been set as reported by LAST.")
    except Exception as e:
        messages.error(request, f"Failed to set reported_by_LAST for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        candidate.refresh_from_db()
        if request.htmx:
            return render_candidate_row_response(request, candidate)
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)


@login_required
def refresh_ztf_view(request, candidate_id):
    """
    Query ZTF for photometry for a candidate.
    Does not add photometry that already exists.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)

    try:
        daysago = request.POST.get('daysago')
        get_ztf_fp(candidate, int(daysago))
        messages.success(request, f"ZTF photometry was updated for {candidate.name}.")
    except Exception as e:
        messages.error(request, f"Failed to refresh ZTF for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        if request.htmx:
            return render_candidate_row_response(request, candidate)
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)


FILTER_MAP = {
    "real": Q(real_bogus=True),
    "bogus": Q(real_bogus=False),
    "neither": Q(real_bogus__isnull=True, classification__isnull=True),
    "tns_reported": Q(reported_by_LAST=True),
    "followup": Q(marked_for_followup=True),
    "tns_not_reported": (
            Q(reported_by_LAST=False, classification__isnull=True)
            & (Q(real_bogus__isnull=True) | Q(real_bogus=True))
    ),
}


def parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from datetime import timedelta


def get_datetime_range(params):
    """
    Normalize start/end datetime range from params.

    Behavior:
    - If start_datetime is missing/empty → default to now()-1 day.
    - If start_datetime is provided but invalid → treat as no lower bound (None).
    - If end_datetime is provided and valid → return parsed datetime.
    - If end_datetime is missing/invalid → return None.
    """
    raw_start = params.get("start_datetime")
    raw_end = params.get("end_datetime")

    # Start datetime
    if raw_start:
        # parse_datetime returns None on invalid format (no exception)
        start_dt = parse_datetime(raw_start)
    else:
        # Default when not provided at all
        start_dt = now() - timedelta(days=1)

    # End datetime
    end_dt = parse_datetime(raw_end) if raw_end else None

    # - if raw_start is provided but invalid -> start_dt is None
    # - no fallback to default in that case (no lower bound)
    if raw_start and start_dt is None:
        # user gave a value but it's invalid -> behave as "no lower bound"
        start_dt = None

    return start_dt, end_dt


CUTOUT_TYPES = ['ps1', 'ref', 'new', 'diff', 'sdss']


def build_cutout_map(candidate_ids):
    latest_cutouts = {}
    cutouts = CandidateDataProduct.objects.filter(
        candidate_id__in=candidate_ids,
        data_product_type__in=CUTOUT_TYPES,
    ).order_by("candidate_id", "data_product_type", "-created_at")

    for cutout in cutouts:
        key = (cutout.candidate_id, cutout.data_product_type)
        if key not in latest_cutouts:
            latest_cutouts[key] = cutout

    return {
        candidate_id: [latest_cutouts.get((candidate_id, cutout_type)) for cutout_type in CUTOUT_TYPES]
        for candidate_id in candidate_ids
    }


def build_target_map(candidates, radius_arcsec=3):
    if not candidates:
        return {}

    radius_deg = radius_arcsec / 3600.0
    ras = [candidate.ra for candidate in candidates]
    decs = [candidate.dec for candidate in candidates]

    nearby_targets = list(
        Target.objects.filter(
            ra__gte=min(ras) - radius_deg,
            ra__lte=max(ras) + radius_deg,
            dec__gte=min(decs) - radius_deg,
            dec__lte=max(decs) + radius_deg,
        )
    )

    if not nearby_targets:
        return {candidate.id: None for candidate in candidates}

    target_coords = SkyCoord(
        ra=[target.ra for target in nearby_targets] * u.deg,
        dec=[target.dec for target in nearby_targets] * u.deg,
    )

    target_map = {}
    max_sep = radius_arcsec * u.arcsec

    for candidate in candidates:
        candidate_coord = SkyCoord(ra=candidate.ra * u.deg, dec=candidate.dec * u.deg)
        separations = candidate_coord.separation(target_coords)

        matched_target = None
        for index, separation in enumerate(separations):
            if separation <= max_sep:
                matched_target = nearby_targets[index]
                break

        target_map[candidate.id] = matched_target

    return target_map


def build_candidate_status_item(candidate):
    return {
        "candidate": candidate,
        "target": check_target_exists_for_candidate(candidate.id),
        "graph": generate_photometry_graph(candidate),
        "cutouts": [
            CandidateDataProduct.objects
            .filter(candidate=candidate, data_product_type=cutout_type)
            .order_by("-created_at")
            .first()
            for cutout_type in CUTOUT_TYPES
        ],
        "last_alert": CandidateAlert.objects
        .filter(candidate=candidate)
        .order_by("-created_at")
        .first(),
        "classification_choices": CLASSIFICATION_CHOICES,
    }


def render_candidate_row_response(request, candidate):
    request_params = extract_params_from_request(request)
    page_number = parse_int(request.GET.get("page")) or 1
    tns_status = get_external_api_service_status("TNS")
    astro_colibri_status = get_external_api_service_status("Astro-COLIBRI")
    context = {
        **request_params,
        "item": build_candidate_status_item(candidate),
        "page_obj": SimpleNamespace(number=page_number),
        "tns_test": settings.TNS_TEST,
        "tns_status": tns_status,
        "astro_colibri_status": astro_colibri_status,
        "tns_enabled": is_external_api_service_enabled("TNS"),
        "astro_colibri_enabled": is_external_api_service_enabled("Astro-COLIBRI"),
    }
    row_html = render_to_string("candidates/partials/_candidate_row.html", context, request=request)
    response = HttpResponse(row_html)
    response["HX-Trigger"] = "refreshMessages"
    return response


def messages_fragment(request):
    return render(request, "tom_common/partials/messages.html")


@login_required
def candidate_list_view(request):
    """
    Display a list of candidates with a filter for real/bogus status.
    """

    request_params = extract_params_from_request(request)
    # Apply filtering based on the filter_value 
    # Base queryset (no ordering yet)
    candidates = Candidate.objects.all()

    filter_value = request_params["filter_value"]

    if filter_value in FILTER_MAP:
        candidates = candidates.filter(FILTER_MAP[filter_value])

    max_distance = parse_float(request_params['max_distance'])
    if max_distance is not None:
        candidates = candidates.filter(dist_Mpc__lte=max_distance)

    fieldid_filter = parse_int(request_params['fieldid_filter'])
    if fieldid_filter is not None:
        candidates = candidates.filter(alert__fieldid=fieldid_filter)

    ToO_filter = request_params['ToO_filter']
    if ToO_filter:
        candidates = candidates.filter(ToO_name__iexact=ToO_filter)

    raw = request_params.get('discovery_date')
    discovery_date = parse_datetime(raw) if raw else None
    if discovery_date is not None:
        candidates = candidates.filter(discovery_datetime__gte=discovery_date)

    # Annotate candidates with the latest alert timestamp
    latest_alert_values = (
        CandidateAlert.objects
        .filter(candidate=OuterRef("pk"))
        .order_by("-created_at")
    )

    candidates = candidates.annotate(
        latest_alert_time=Subquery(latest_alert_values.values("created_at")[:1]),
        latest_alert_score=Subquery(latest_alert_values.values("score")[:1]),
        latest_alert_mount=Subquery(latest_alert_values.values("mount")[:1]),
        latest_alert_camera=Subquery(latest_alert_values.values("camera")[:1]),
        latest_alert_fieldid=Subquery(latest_alert_values.values("fieldid")[:1]),
        latest_alert_subimage=Subquery(latest_alert_values.values("subimage")[:1]),
    )
    candidates = candidates.exclude(latest_alert_time__isnull=True)

    start_datetime, end_datetime = get_datetime_range(request_params)

    # Apply datetime filtering
    if start_datetime:
        candidates = candidates.filter(latest_alert_time__gte=start_datetime)
    if end_datetime:
        candidates = candidates.filter(latest_alert_time__lte=end_datetime)

    candidates = candidates.order_by("-latest_alert_time")
    candidates = candidates.exclude(id__isnull=True)
    # Apply pagination
    items_per_page = parse_int(request_params.get("items_per_page")) or 25
    paginator = Paginator(candidates, items_per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    page_candidates = list(page_obj.object_list)
    candidate_ids = [c.id for c in page_candidates]
    cutout_map = build_cutout_map(candidate_ids)
    target_map = build_target_map(page_candidates)

    candidate_status = [
        {
            "candidate": candidate,
            "target": target_map.get(candidate.id),
            "graph": generate_photometry_graph(candidate),
            "cutouts": cutout_map.get(candidate.id, []),
            "last_alert": {
                "score": candidate.latest_alert_score,
                "mount": candidate.latest_alert_mount,
                "camera": candidate.latest_alert_camera,
                "fieldid": candidate.latest_alert_fieldid,
                "subimage": candidate.latest_alert_subimage,
                "created_at": candidate.latest_alert_time,
            },
            "classification_choices": CLASSIFICATION_CHOICES,
        }
        for candidate in page_candidates
    ]

    # Construct query string without 'page' parameter for pagination links
    parsed = urlparse(request.get_full_path())
    qs = parse_qs(parsed.query)
    qs.pop('page', None)
    query_without_page = urlencode(qs, doseq=True)

    context = {
        **request_params,
        "tns_status": get_external_api_service_status("TNS"),
        "astro_colibri_status": get_external_api_service_status("Astro-COLIBRI"),
        "tns_enabled": is_external_api_service_enabled("TNS"),
        "astro_colibri_enabled": is_external_api_service_enabled("Astro-COLIBRI"),

        'start_datetime': start_datetime,  # override in case not supplied, and then changed to "now"
        'candidate_status': candidate_status,
        'candidate_count': paginator.count,
        'page_obj': page_obj,
        'query_without_page': query_without_page,
        'tns_test': settings.TNS_TEST,
    }

    return render(request, 'candidates/list.html', context)


@login_required
def add_target_view(request):
    """
    Adds a candidate as a TOM target.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')
        return_url = _safe_return_url(request)

        try:
            target = add_candidate_as_target(candidate_id)
            messages.success(request, f"Candidate added as target: {target.name}")
        except Exception as e:
            messages.error(request, f"Failed to add target: {str(e)}")

        # Append anchor to scroll back to the candidate
        if candidate_id:
            if request.htmx:
                candidate = get_object_or_404(Candidate, id=candidate_id)
                return render_candidate_row_response(request, candidate)
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')


@login_required
def update_real_bogus_view(request, candidate_id):
    """
    Updates the real/bogus status of a candidate based on the button clicked.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        real_bogus = request.POST.get('real_bogus')
        return_url = _safe_return_url(request)

        # Map the input to the appropriate value
        if real_bogus == 'real':
            candidate.real_bogus = True
        elif real_bogus == 'bogus':
            candidate.real_bogus = False
        elif real_bogus == 'null':
            candidate.real_bogus = None
        else:
            messages.error(request, "Invalid real/bogus value selected.")
            return redirect('candidates:list')
        user = request.user
        full_name = f"{user.first_name} {user.last_name}".strip()
        candidate.real_bogus_user = full_name or user.username

        candidate.save()

        if candidate.real_bogus is True:
            existing_target = check_target_exists_for_candidate(candidate.id)
            try:
                target = add_candidate_as_target(candidate.id)
            except Exception as e:
                messages.error(
                    request,
                    f"Updated {candidate.name} to {candidate.get_real_bogus_display()}, but failed to ensure a target exists: {e}"
                )
            else:
                action = "reused" if existing_target else "created"
                messages.success(
                    request,
                    f"Updated {candidate.name} to {candidate.get_real_bogus_display()} and {action} target {target.name}."
                )
        else:
            messages.success(request, f"Updated {candidate.name} to {candidate.get_real_bogus_display()}.")

        # Append anchor to scroll back to the candidate
        if candidate_id:
            if request.htmx:
                return render_candidate_row_response(request, candidate)
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')


@login_required
def update_classification_view(request, candidate_id):
    """
    Updates the classification status of a candidate based on the button clicked.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        return_url = _safe_return_url(request)
        classification = request.POST.get('classification')
        if classification == 'null':
            candidate.classification = None
            candidate.save()
            messages.success(request, f"Reset {candidate.name} classification.")
        else:
            candidate.classification = classification
            candidate.save()
            messages.success(request, f"Updated {candidate.name} classification to {classification}.")

        # Append anchor to scroll back to the candidate
        if candidate_id:
            if request.htmx:
                return render_candidate_row_response(request, candidate)
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')


@login_required
@user_passes_test(lambda user: user.groups.filter(name='LAST general').exists())
def update_followup_view(request, candidate_id):
    """
    Mark or unmark a candidate as 'marked_for_followup'.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        followup_action = request.POST.get('followup')

        if followup_action == 'mark':
            candidate.marked_for_followup = True
            msg = f"{candidate.name} marked for follow-up."
        elif followup_action == 'unmark':
            candidate.marked_for_followup = False
            msg = f"{candidate.name} unmarked for follow-up."
        else:
            messages.error(request, "Invalid follow-up action.")
            return redirect('candidates:list')

        candidate.save()
        messages.success(request, msg)

        if request.htmx:
            return render_candidate_row_response(request, candidate)

    # Keep the same redirect pattern you use everywhere else
    filter_value = request.GET.get('filter', 'all')
    redirect_url = f"{reverse('candidates:list')}?filter={filter_value}"
    start_datetime = request.GET.get('start_datetime', '')
    end_datetime = request.GET.get('end_datetime', '')
    page = request.GET.get('page', '')
    items_per_page = request.GET.get('items_per_page', 25)

    if start_datetime:
        redirect_url += f"&start_datetime={start_datetime}"
    if end_datetime:
        redirect_url += f"&end_datetime={end_datetime}"
    if page:
        redirect_url += f"&page={page}"
    if items_per_page:
        redirect_url += f"&items_per_page={items_per_page}"

    redirect_url += f"#candidate-{candidate_id}"

    return redirect(redirect_url)


@tns_permission_required
def send_tns_report_view(request, candidate_id):
    """
    Generates and sends a TNS report for a candidate.
    """
    comment = request.POST.get('comment', '').strip()
    at_type = request.POST.get('at_type', None)
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)

    missing_name_response = _missing_reporter_name_response(request, return_url)
    if missing_name_response is not None:
        return missing_name_response

    try:
        user = request.user
        if comment:
            send_tns_report(candidate, user.first_name, user.last_name, at_type, comment)
        else:
            send_tns_report(candidate, user.first_name, user.last_name, at_type)
        # Add a success message with the candidate name being a link to the candidate detail page
        messages.success(
            request,
            mark_safe(f"TNS report successfully sent for <a href='/candidates/{candidate.pk}/'>{candidate.name}</a>.")
        )

    except Exception as e:
        messages.error(request, f"Failed to send TNS report for {candidate.name}: {e}")

    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))
    return redirect(return_url)


@tns_permission_required
def tns_report_view(request, candidate_id):
    """
    View for displaying TNS report details and manually sending the report.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    missing_name_response = _missing_reporter_name_response(request, return_url)
    if missing_name_response is not None:
        return missing_name_response

    user = request.user
    report = tns_report_details(candidate, user.first_name, user.last_name)
    at_report = report.get('at_report', {})

    report_details = {
        "RA": at_report['RA']['value'],
        "DEC": at_report['Dec']['value'],
        "discovery_datetime": at_report['discovery_datetime'][0],
        "last_non_detection": at_report['non_detection']['obsdate'][0],
        "non_detection_limit": at_report['non_detection']['flux'],
        "detection_mag": at_report['photometry']['photometry_group']['flux'],
        "reporters": at_report['reporter'],
    }

    return render(request, 'candidates/tns_report_details.html', {
        'candidate': candidate,
        'report_details': report_details,
        'return_url': return_url,
        'TNS_TEST': settings.TNS_TEST,
    })


@login_required
def update_cutouts_view(request, candidate_id):
    """
    Updates the cutouts for a candidate.
    TODO: I think this is unused, make sure before removing this method
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    filter_value = request.GET.get('filter', 'all')  # Get the current filter from the query parameters

    try:
        # Example: Assume a function `send_tns_report(candidate)` sends the report
        update_candidate_cutouts(candidate)
        messages.success(request, f"cutouts have been updated for {candidate.name}.")
    except Exception as e:
        messages.error(request, f"Failed to update cutouts for {candidate.name}: {e}")

    # Redirect back to the filtered candidate list
    return redirect(f"{reverse('candidates:list')}?filter={filter_value}")


def candidate_detail(request, candidate_id):
    request_params = extract_params_from_request(request)
    candidate = get_object_or_404(Candidate, id=candidate_id)

    # Get the candidate's coordinates
    coord = SkyCoord(ra=candidate.ra * u.degree, dec=candidate.dec * u.degree, frame='icrs')
    ra_hms = coord.ra.to_string(unit=u.hour, sep=':', precision=2, pad=False)  # RA in hh:mm:ss.ss
    dec_dms = coord.dec.to_string(unit=u.degree, sep=':', precision=2, alwayssign=True, pad=False)  # Dec in dd:mm:ss.ss
    l = coord.galactic.l.degree
    b = coord.galactic.b.degree
    coords = {'l': l, 'b': b, 'ra_hms': ra_hms, 'dec_dms': dec_dms}

    # Separate PS1 & SDSS cutouts
    ps1_cutout = candidate.data_products.filter(data_product_type="ps1").first()
    sdss_cutout = candidate.data_products.filter(data_product_type="sdss").first()
    # Group cutouts by the minute they were created
    grouped_cutouts = defaultdict(list)

    for cutout in candidate.data_products.filter(data_product_type__in=['ref', 'new', 'diff']):
        cutout_time = cutout.created_at.strftime("%Y-%m-%d %H:%M")  # Extract minute
        grouped_cutouts[cutout_time].append(cutout)

    context = {
        **request_params,
        'candidate': candidate,
        'photometry_graph': generate_photometry_graph(candidate),
        'ps1_cutout': ps1_cutout,
        'sdss_cutout': sdss_cutout,
        'grouped_cutouts': dict(sorted(grouped_cutouts.items(), reverse=True)),  # Sort by newest first
        'coords': coords,
    }
    return render(request, 'candidates/candidate_detail.html', context)


def candidate_comments_view(request, candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    next_url = request.POST.get("next") or request.GET.get("next") or reverse(
        "candidates:candidate_detail", args=[candidate.id]
    )

    comment_error = None
    if request.method == "POST":
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Authentication required to comment.")

        comment_text = request.POST.get("comment", "").strip()
        if not comment_text:
            comment_error = "Comment cannot be empty."
        else:
            Comment.objects.create(
                content_type=ContentType.objects.get_for_model(candidate),
                object_pk=str(candidate.pk),
                user=request.user,
                user_name=request.user.get_full_name() or request.user.get_username(),
                user_email=request.user.email,
                comment=comment_text,
                site=get_current_site(request),
            )

        if not request.htmx:
            return redirect(next_url)

    return render_candidate_comments_response(
        request,
        candidate,
        next_url=next_url,
        comment_error=comment_error,
    )


def render_candidate_comments_response(request, candidate, *, next_url=None, comment_error=None):
    comment_list = Comment.objects.filter(
        content_type=ContentType.objects.get_for_model(candidate),
        object_pk=str(candidate.pk),
        is_public=True,
        is_removed=False,
    ).order_by("submit_date")
    return render(
        request,
        "candidates/partials/_candidate_comments_section.html",
        {
            "comment_candidate": candidate,
            "comment_list": comment_list,
            "comment_error": comment_error,
            "comment_next_url": next_url or request.get_full_path(),
        },
    )


def horizons_view(request, candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))
    try:
        data = get_horizons_data(candidate_id)
        if data and data['n_second_pass'] > 0:
            results = [
                {
                    "object_name": row[0],
                    "dist_norm": row[5],
                    "visual_mag": row[6],
                    "ra_rate": row[7],
                    "dec_rate": row[8],
                }
                for row in data["data_second_pass"]
            ]
            results = sorted(results, key=lambda x: float(x["dist_norm"]))
        else:
            results = []
    except Exception as e:
        messages.error(request, f"Failed to get data from Horizons: {e}")
        return redirect('candidates:list')

    context = {
        'candidate_name': candidate.name,
        'results': results,
        'return_url': return_url
    }
    return render(request, 'candidates/horizon.html', context)


@login_required
def send_astro_colibri_view(request, candidate_id):
    """
    Send candidate data to Astro Colibri.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))
    try:
        # Ideally, this data should be provided from astro_colbri_report after
        # already prepared there. For a lack of a better solution, we prepare 
        # it again here, same as in tns_report_details that's called twice.
        data = prepare_astro_colibri_data(candidate)
        send_astro_colibri(data)
        candidate.reported_to_astro_colibri = True
        candidate.save()
        messages.success(request, f"Candidate {candidate.name} sent to Astro Colibri.")
        if request.htmx:
            return render_candidate_row_response(request, candidate)
    except Exception as e:
        messages.error(request, f"Failed to send candidate to Astro-COLIBRI: {e}")

    return redirect(return_url)


@login_required
def astro_colibri_report(request, candidate_id):
    """
    View for displaying Astro-COLIBRI report details and manually sending the report.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = _safe_return_url(request)
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    report_details = prepare_astro_colibri_data(candidate)

    return render(request, 'candidates/astro_colibri_report.html', {
        'candidate': candidate,
        'report_details': report_details,
        'return_url': return_url,
    })
