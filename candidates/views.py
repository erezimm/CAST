# Built-in imports
from collections import defaultdict
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

# Third-party imports
from astropy.coordinates import SkyCoord
import astropy.units as u

# Django imports
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.core.paginator import Paginator
from datetime import timedelta, datetime
from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from django.db.models import Subquery, OuterRef
from django.contrib.auth.decorators import login_required, user_passes_test
from django.conf import settings
from django.db.models import Q

 # Local imports
from .forms import FileUploadForm
from .utils import process_json_file, add_candidate_as_target, check_target_exists_for_candidate,\
                   send_tns_report,update_candidate_cutouts,tns_report_details,\
                   get_horizons_data, set_reported_by_LAST
from .models import Candidate,CandidateDataProduct,CandidateAlert
from .photometry_utils import generate_photometry_graph, get_atlas_fp, get_ztf_fp


def extract_params_from_request(request):
    return {
        'filter_value': request.GET.get('filter', 'all'),
        'start_datetime': request.GET.get('start_datetime'),
        'end_datetime': request.GET.get('end_datetime'),
        'max_distance': request.GET.get('max_distance', ''),
        'fieldid_filter': request.GET.get('fieldid_filter', ''),
        'ToO_filter': request.GET.get('ToO_filter', ''),
        'discovery_date': request.GET.get('discovery_date', ''),
        'items_per_page': request.GET.get('items_per_page', 25),
        'return_url': request.get_full_path(),
    }


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


def delete_candidate_view(request):
    """
    Handles deletion of a candidate via form submission.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')  # Get candidate ID from the form
        candidate = get_object_or_404(Candidate, id=candidate_id)
        return_url = request.POST.get('return_url', reverse('candidates:list'))
        
        # Delete the candidate
        candidate_name = candidate.name
        candidate.delete()
        
        # Add a success message
        messages.success(request, f"Candidate '{candidate_name}' has been deleted.")

        return redirect(return_url)
    
    # Redirect back to the candidate list
    return redirect('candidates:list')


def refresh_atlas_view(request, candidate_id):
    """
    Query Atlas for photometry for a candidate.
    Does not add photometry that already exists.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))
    try:
        daysago = request.POST.get('daysago')
        get_atlas_fp(candidate, int(daysago))
        messages.success(request, f"Atlas photometry was updated for {candidate.name}.")
    except Exception as e:
        messages.error(request, f"Failed to refresh Atlas for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)

def set_reported_by_last_view(request, candidate_id):
    """
    Set the reported_by_LAST field for a candidate.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))
    try:
        set_reported_by_LAST(candidate_id)
        messages.success(request, f"Candidate {candidate.name} has been set as reported by LAST.")
    except Exception as e:
        messages.error(request, f"Failed to set reported_by_LAST for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)

def refresh_ztf_view(request, candidate_id):
    """
    Query ZTF for photometry for a candidate.
    Does not add photometry that already exists.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))

    try:
        daysago = request.POST.get('daysago')
        get_ztf_fp(candidate, int(daysago))
        messages.success(request, f"ZTF photometry was updated for {candidate.name}.")
    except Exception as e:
        messages.error(request, f"Failed to refresh ZTF for {candidate.name}: {e}")

    # Add anchor for the specific candidate
    if candidate_id:
        parsed = urlparse(return_url)
        return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    return redirect(return_url)


@login_required
@user_passes_test(lambda user: user.groups.filter(name='LAST general').exists())
def candidate_list_view(request):
    """
    Display a list of candidates with a filter for real/bogus status.
    """
    
    request_params = extract_params_from_request(request)

    filter_value = request_params['filter_value']
    
    # Apply filtering based on the filter_value 
    if filter_value == 'real':
        candidates = Candidate.objects.filter(real_bogus=True).order_by('-created_at')
    elif filter_value == 'bogus':
        candidates = Candidate.objects.filter(real_bogus=False).order_by('-created_at')
    elif filter_value == 'neither':
        candidates = Candidate.objects.filter(real_bogus__isnull=True, classification__isnull=True).order_by('-created_at')
    elif filter_value == 'tns_reported':
        candidates = Candidate.objects.filter(reported_by_LAST=True).order_by('-created_at')
    elif filter_value == 'tns_not_reported':
        # Filter candidates that are not reported by LAST and not bogus but could be real and has not classification
        candidates = Candidate.objects.filter(reported_by_LAST=False, classification__isnull=True).filter(Q(real_bogus__isnull=True) | Q(real_bogus=True)).order_by('-created_at')
    else:  # 'all'
        candidates = Candidate.objects.all().order_by('-created_at')

    max_distance = request_params['max_distance']
    if max_distance:
        try:
            max_distance = float(max_distance)
            candidates = candidates.filter(dist_Mpc__lte=max_distance)
        except ValueError:
            pass  # Ignore invalid input

    fieldid_filter = request_params['fieldid_filter']
    if fieldid_filter:
        try:
            fieldid_filter = int(fieldid_filter)
            candidates = candidates.filter(alert__fieldid=fieldid_filter)
        except ValueError:
            pass  # Ignore invalid input

    ToO_filter = request_params['ToO_filter']
    if ToO_filter:
        candidates = candidates.filter(ToO_name__iexact=ToO_filter)

    discovery_date = request_params['discovery_date']
    if discovery_date:
        try:
            # Convert to date/datetime if needed
            discovery_date = parse_datetime(discovery_date)
            candidates = candidates.filter(discovery_datetime__gte=discovery_date)
        except ValueError:
            pass  # invalid date format, ignore
    # Annotate candidates with the latest alert timestamp
    candidates = candidates.annotate(
        latest_alert_time=Subquery(
            CandidateAlert.objects.filter(candidate=OuterRef('pk'))
            .order_by('-created_at')
            .values('created_at')[:1]
        )
    ).order_by('-latest_alert_time')

    # Get filter values from the request
    start_datetime = request_params['start_datetime']
    end_datetime = request_params['end_datetime']
    if start_datetime:
        start_datetime = parse_datetime(start_datetime)
    # If parsing fails or no value is provided, apply default values
    else:
        current_time = now()
        previous_day = (current_time - timedelta(days=1))
        start_datetime = previous_day

    if end_datetime:
        end_datetime = parse_datetime(end_datetime)

    # Apply datetime filtering
    if start_datetime:
        candidates = candidates.filter(latest_alert_time__gte=start_datetime)
    if end_datetime:
        candidates = candidates.filter(latest_alert_time__lte=end_datetime)
    
    cutout_types = ['ps1', 'ref', 'new', 'diff','sdss']
    
    candidate_status = [
        {
            'candidate': candidate,
            'target': check_target_exists_for_candidate(candidate.id),  # Include Target if it exists
            'graph': generate_photometry_graph(candidate),  # Generate photometry graph
            'cutouts': [
                CandidateDataProduct.objects.filter(candidate=candidate, data_product_type=cutout_type)
                    .order_by('-created_at')
                    .first()
                for cutout_type in cutout_types
            ],
            'last_alert': CandidateAlert.objects.filter(candidate=candidate).order_by('-created_at').first(),
        }
        for candidate in candidates
    ]

    # Apply pagination
    items_per_page = request_params['items_per_page']
    try:
        items_per_page = int(items_per_page)
    except ValueError:
        items_per_page = 25
    paginator = Paginator(candidate_status, items_per_page) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        **request_params,
        'start_datetime': start_datetime,  # override in case not supplied, and then changed to "now"
        'candidate_status': candidate_status,
        'candidate_count': candidates.count(),
        'page_obj': page_obj,
        'tns_test': settings.TNS_TEST,
    }
    return render(request, 'candidates/list.html', context)

def add_target_view(request):
    """
    Adds a candidate as a TOM target.
    """
    if request.method == 'POST':
        candidate_id = request.POST.get('candidate_id')
        return_url = request.POST.get('return_url', reverse('candidates:list'))

        try:
            target = add_candidate_as_target(candidate_id)
            messages.success(request, f"Candidate added as target: {target.name}")
        except Exception as e:
            messages.error(request, f"Failed to add target: {str(e)}")
        
        # Append anchor to scroll back to the candidate
        if candidate_id:
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')

def update_real_bogus_view(request, candidate_id):
    """
    Updates the real/bogus status of a candidate based on the button clicked.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        real_bogus = request.POST.get('real_bogus')
        return_url = request.POST.get('return_url', reverse('candidates:list'))
        
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
        candidate.real_bogus_user = f"{user.first_name} {user.last_name}"

        candidate.save()
        messages.success(request, f"Updated {candidate.name} to {candidate.get_real_bogus_display()}.")

        # Append anchor to scroll back to the candidate
        if candidate_id:
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')


def update_classification_view(request, candidate_id):
    """
    Updates the classification status of a candidate based on the button clicked.
    """
    if request.method == 'POST':
        candidate = get_object_or_404(Candidate, id=candidate_id)
        return_url = request.POST.get('return_url', reverse('candidates:list'))
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
            parsed = urlparse(return_url)
            return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

        return redirect(return_url)

    return redirect('candidates:list')

@login_required
@user_passes_test(lambda user: user.groups.filter(name='LAST general').exists())
def send_tns_report_view(request, candidate_id):
    """
    Generates and sends a TNS report for a candidate.
    """
    comment = request.POST.get('comment', '').strip()
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))
    print("I got the return url:", return_url)

    try:
        user = request.user
        if comment:
            send_tns_report(candidate,user.first_name,user.last_name,comment)
        else:
            send_tns_report(candidate,user.first_name,user.last_name)
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

@login_required
@user_passes_test(lambda user: user.groups.filter(name='LAST general').exists())
def tns_report_view(request, candidate_id):
    """
    View for displaying TNS report details and manually sending the report.
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))

    user = request.user
    report = tns_report_details(candidate,user.first_name,user.last_name)
    at_report = report.get('at_report', {})
    
    report_details = {
        "RA": at_report['RA']['value'],
        "DEC": at_report['Dec']['value'],
        "discovery_datetime": at_report['discovery_datetime'][0],
        "last_non_detection" : at_report['non_detection']['obsdate'][0],
        "non_detection_limit": at_report['non_detection']['flux'],
        "detection_mag" : at_report['photometry']['photometry_group']['flux'],
        "reporters" : at_report['reporter'],
    }

    return render(request, 'candidates/tns_report_details.html', {
        'candidate': candidate,
        'report_details': report_details,
        'return_url': return_url,
        'TNS_TEST': settings.TNS_TEST,
    })

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
    coord = SkyCoord(ra=candidate.ra*u.degree, dec=candidate.dec*u.degree, frame='icrs')
    ra_hms = coord.ra.to_string(unit=u.hour, sep=':', precision=2, pad=False)  # RA in hh:mm:ss.ss
    dec_dms = coord.dec.to_string(unit=u.degree, sep=':', precision=2, alwayssign=True, pad=False)  # Dec in dd:mm:ss.ss
    l = coord.galactic.l.degree
    b = coord.galactic.b.degree
    coords = {'l': l, 'b': b,'ra_hms': ra_hms, 'dec_dms': dec_dms}

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

def horizons_view(request, candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return_url = request.POST.get('return_url', reverse('candidates:list'))
    parsed = urlparse(return_url)
    return_url = urlunparse(parsed._replace(fragment=f"candidate-{candidate_id}"))
    print("I got the return url:", return_url)
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
        'results' : results,
        'return_url': return_url
    }
    return render(request, 'candidates/horizon.html', context)