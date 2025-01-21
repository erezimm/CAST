from .models import Candidate,CandidatePhotometry
from tom_targets.models import Target
from tom_targets.utils import cone_search_filter
from django.shortcuts import get_object_or_404
from django.db.models import ExpressionWrapper, FloatField
from django.db.models.functions import ACos, Cos, Radians, Pi, Sin
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.utils.timezone import now
from django.utils.safestring import mark_safe
from math import radians
import os
import json
import requests
import plotly.graph_objs as go

def cone_search_filter_candidates(queryset, ra, dec, radius):
    """
    Executes cone search by annotating each candidate with separation distance from the specified RA/Dec.
    Formula is based on the Angular Distance formula:
    https://en.wikipedia.org/wiki/Angular_distance

    :param queryset: Queryset of Candidate objects.
    :type queryset: QuerySet

    :param ra: Right ascension of center of cone (in degrees).
    :type ra: float

    :param dec: Declination of center of cone (in degrees).
    :type dec: float

    :param radius: Radius of cone search (in degrees).
    :type radius: float

    :return: Filtered queryset of candidates within the cone search radius.
    :rtype: QuerySet
    """
    ra = float(ra)
    dec = float(dec)
    radius = float(radius)

    # Double radius for the bounding box square search
    double_radius = radius * 2

    # Square pre-filter: limit candidates to a bounding box to improve performance
    # queryset = queryset.filter(
    #     ra__gte=ra - double_radius, ra__lte=ra + double_radius,
    #     dec__gte=dec - double_radius, dec__lte=dec + double_radius
    # )
    # Angular separation calculation
    separation = ExpressionWrapper(
        180 * ACos(
            (Sin(radians(dec)) * Sin(Radians('dec'))) +
            (Cos(radians(dec)) * Cos(Radians('dec')) * Cos(radians(ra) - Radians('ra')))
        ) / Pi(), FloatField()
    )

    # Annotate queryset with separation and filter by the radius
    return queryset.annotate(separation=separation).filter(separation__lte=radius)

def check_candidate_exists_by_cone(ra, dec, radius_arcsec=3):
    """
    Checks if a candidate already exists in the database within a given radius.
    :param ra: Right Ascension of the candidate in degrees.
    :param dec: Declination of the candidate in degrees.
    :param radius_arcsec: Radius of the cone search in arcseconds.
    :return: True if a candidate exists within the radius, False otherwise.
    """
    # Convert radius from arcseconds to degrees
    radius_deg = radius_arcsec / 3600.0

    # Perform the cone search
    queryset = Candidate.objects.all()
    matching_candidates = cone_search_filter_candidates(queryset, ra, dec, radius_deg)

    # Return True if any matching candidates are found, False otherwise
    return matching_candidates.exists()

def process_json_file(file):
    """
    Processes the uploaded JSON file and adds candidates to the database.
    :param file: File object uploaded by the user
    :return: The number of candidates successfully added
    """
    try:
        # Read and decode the file content before parsing as JSON
        file_content = file.read().decode('utf-8')  # Decode to string
        data = json.loads(file_content)  # Parse the JSON content
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")
    except Exception as e:
        raise ValueError(f"Error reading file: {e}")

    candidates_added = 0


    at_report = data.get('at_report', {})
    ra = at_report.get('RA', {}).get('value')
    dec = at_report.get('Dec', {}).get('value')
    # name = f"LAST_{ra}_{dec}"  # Default name (or extract from JSON if available)
    discovery_datetime = at_report.get('discovery_datetime',{})[0]
    discovery_datetime = discovery_datetime[:discovery_datetime.find('UTC')-1]
    
    if check_candidate_exists_by_cone(float(ra), float(dec), radius_arcsec=3):
        return None

    if ra and dec:
        # Save to the database
        candidate = Candidate.objects.create(ra=ra, dec=dec,discovery_datetime=discovery_datetime)
        candidates_added += 1

        # 1. Handle non-detection photometry
        non_detection = at_report.get('non_detection', {})
        if non_detection:
            obs_date = parse_datetime(non_detection.get('obsdate', [None])[0].replace(" UTC", ""))
            limit = non_detection.get('flux', None)
            filter_value = non_detection.get('filter_value', None)
            telescope = "LAST"  # You can map instrument_value to actual telescope name
            instrument = "LAST-CAM"  # You can map instrument_value to actual instrument name

            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=obs_date,
                limit = limit,
                filter_band=str(filter_value),  # Use a mapping if needed to human-readable filter names
                telescope=telescope,
                instrument=instrument
            )
            
        photometry_data = at_report.get('photometry', {}).get('photometry_group', {})
        obs_date = photometry_data.get('obsdate', [None])[0]
        if obs_date:
            obs_date = parse_datetime(obs_date.replace(" UTC", ""))
            magnitude = photometry_data.get('flux', None)
            magnitude_error = 0  # Add logic to calculate or store flux error if available
            filter_value = photometry_data.get('filter_value', None)
            telescope = "LAST"  # You can map instrument_value to actual telescope name
            instrument = "LAST-CAM"  # You can map instrument_value to actual instrument name

            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=obs_date,
                magnitude=magnitude,
                magnitude_error=magnitude_error,
                filter_band=str(filter_value),  # Use a mapping if needed to human-readable filter names
                telescope=telescope,
                instrument=instrument
            )

    return candidates_added

def process_multiple_json_files(directory_path):
    """
    Processes multiple JSON files from a given directory and adds candidates to the database.
    :param directory_path: Path to the directory containing JSON files
    :return: The total number of candidates successfully added
    """
    json_files = [f for f in os.listdir(directory_path) if f.endswith('.json')]
    total_candidates_added = 0

    for json_file in json_files:
        file_path = os.path.join(directory_path, json_file)
        try:
            with open(file_path, 'rb') as file:  # Open each file in binary mode
                candidates_added = process_json_file(file)
                if candidates_added:
                    print(f"Processed {json_file}: {candidates_added} candidate(s) added.")
                    total_candidates_added += candidates_added
                else:
                    print(f"Skipping {json_file}: Candidate already exists or no RA/Dec found.")
        except Exception as e:
            print(f"Error processing {json_file}: {e}")

    print(f"Total candidates added: {total_candidates_added}")
    return total_candidates_added

def check_target_exists_for_candidate(candidate_id, radius_arcsec=3):
    """
    Checks if a target exists for a given candidate using a cone search.
    :param candidate_id: The ID of the candidate.
    :param radius_arcsec: Radius of the cone search in arcseconds (default is 3").
    :return: The first matching Target object or None if no match is found.
    """
    # Get the candidate
    candidate = get_object_or_404(Candidate, id=candidate_id)

    # Convert radius to degrees
    radius_deg = radius_arcsec / 3600.0

    # Perform a cone search around the candidate's RA/Dec
    queryset = Target.objects.all()
    matching_targets = cone_search_filter(queryset, float(candidate.ra), float(candidate.dec), radius_deg)

    # Return the first matching target if any, or None
    return matching_targets.first()

def add_candidate_as_target(candidate_id):
    """
    Adds a candidate as a TOM target if it doesn't already exist.
    :param candidate_id: The ID of the candidate to add as a target.
    :return: The newly created or existing Target object.
    """
    existing_target = check_target_exists_for_candidate(candidate_id)
    if existing_target:
        return existing_target  # If target exists, return it without creating a new one

    candidate = get_object_or_404(Candidate, id=candidate_id)
    target = Target.objects.create(
        name=candidate.name,
        type=Target.SIDEREAL,  # Assuming sidereal targets
        ra=candidate.ra,
        dec=candidate.dec,
    )
    return target


def generate_photometry_graph(candidate):
    """
    Generate a photometry graph for a candidate using Plotly, showing days ago on the x-axis (reversed).
    :param candidate: Candidate instance
    :return: Safe HTML string for Plotly graph or None if no photometry exists
    """
    # Get photometry data for the candidate
    photometry = CandidatePhotometry.objects.filter(candidate=candidate).order_by('obs_date')

    if not photometry.exists():
        return None  # If no photometry data, return None

    # Separate detections (magnitude is not empty) and non-detections (magnitude is empty)
    detections = photometry.exclude(magnitude__isnull=True)
    non_detections = photometry.filter(magnitude__isnull=True)

    # Get the current timezone-aware datetime
    today = now()

    # Prepare data for detections
    detection_days_ago = [(today - p.obs_date).days for p in detections]
    detection_magnitudes = [p.magnitude for p in detections]
    detection_errors = [p.magnitude_error for p in detections]

    # Prepare data for non-detections
    non_detection_days_ago = [(today - p.obs_date).days for p in non_detections]
    non_detection_limits = [p.limit for p in non_detections]

    # Create the Plotly graph
    fig = go.Figure()

    # Add detections as scatter points with error bars
    fig.add_trace(go.Scatter(
        x=detection_days_ago,
        y=detection_magnitudes,
        error_y=dict(
            type='data',
            array=detection_errors,
            visible=True
        ),
        mode='markers+lines',
        marker=dict(symbol='circle', size=8, color='#636efa'),
        name=f"Detections"
    ))

    # Add non-detections as downward arrows
    fig.add_trace(go.Scatter(
        x=non_detection_days_ago,
        y=non_detection_limits,
        mode='markers',
        marker=dict(symbol='triangle-down', size=8, color='#636efa'),
        name=f"Non-Detections"
    ))

    # Customize layout
    fig.update_layout(
        xaxis_title="Days Ago",
        yaxis_title="Magnitude",
        yaxis=dict(autorange="reversed"),  # Magnitude is brighter for lower values
        xaxis=dict(
            title="Days Ago",
            dtick=10,  # Adjust tick spacing as needed
            autorange="reversed",  # Reverse the x-axis
        ),
        margin=dict(l=50, r=0, t=10, b=100),  # Tight margins
        paper_bgcolor="rgba(0,0,0,0)",  # Transparent outer background
        template="plotly_white",
        showlegend=False,  # Disable the legend
    )

    # Convert the graph to HTML for embedding in the template
    graph_html = fig.to_html(full_html=False, include_plotlyjs='cdn')

    return mark_safe(graph_html)

def tns_cone_search(ra, dec, radius=3.0):
    """
    Perform a cone search on the Transient Name Server.

    Parameters:
        ra (float): Right Ascension in degrees.
        dec (float): Declination in degrees.
        radius (float): Search radius in arcminutes.

    Returns:
        dict: The response data from the TNS.
    """
    # API endpoint for the cone searchtns_settings = settings.BROKERS.get('TNS', {})
    TNS                 = "www.sandbox.wis-tns.org"
    url_tns_api         = "https://" + TNS + "/api"
    tns_settings = settings.BROKERS.get('TNS', {})
    TNS_BOT_ID          = tns_settings.get('bot_id')
    TNS_BOT_NAME        = tns_settings.get('bot_name')
    TNS_API_KEY         = tns_settings.get('api_key')

    endpoint = f"{url_tns_api}/get/search"
    
    # Headers required for the TNS API
    tns_marker = 'tns_marker{"tns_id": "' + str(TNS_BOT_ID) + '", "type": "bot", "name": "' + TNS_BOT_NAME + '"}'
    headers = {'User-Agent': tns_marker}

    # Parameters for the cone search
    payload = {
        "api_key": TNS_API_KEY,
        "data": json.dumps({
            "ra": str(ra),
            "dec": str(dec),
            "radius": str(radius),
            "units": "arcmin"
        })
    }
    # Perform the request
    try:
        response = requests.post(endpoint, headers=headers, data=payload)
        response.raise_for_status()  # Raise an error for bad status codes
        return response.json()  # Parse the JSON response
    except requests.RequestException as e:
        print(f"Error during TNS cone search: {e}")
        return None

def send_tns_report(candidate):
    return None

