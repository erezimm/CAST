from types import SimpleNamespace
from .models import Candidate,CandidatePhotometry,CandidateDataProduct,CandidateAlert
from tom_targets.models import Target
# from tom_targets.utils import cone_search_filter
from django.shortcuts import get_object_or_404
from django.db.models import ExpressionWrapper, FloatField
from django.db.models.functions import ACos, Cos, Radians, Pi, Sin
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.core.files import File  # Import the File wrapper
from django.utils.timezone import now, make_aware#,get_current_timezone,make_aware,is_aware
from django.utils.safestring import mark_safe
from django.contrib.auth.models import Group
from guardian.shortcuts import assign_perm
from django.core.files.base import ContentFile
# from django.http import HttpResponse
from tom_dataproducts.models import ReducedDatum
from math import radians
import os
import io
import re
import json
import requests
from datetime import datetime,timedelta
import glob
from io import BytesIO,StringIO
import plotly.graph_objs as go
from plotly.colors import hex_to_rgb, unlabel_rgb
from astropy.time import Time
from django.core.files.base import ContentFile
import numpy as np
import pandas as pd
from collections import OrderedDict
import time
import traceback
from astropy.coordinates import SkyCoord
import astropy.units as u
import pandas as pd


ATLAS_BASEURL = "https://fallingstar-data.com/forcedphot"


def cone_search_filter(queryset, ra, dec, radius):
    """
    Executes cone search by annotating each target with separation distance from the specified RA/Dec.
    Formula is from Wikipedia: https://en.wikipedia.org/wiki/Angular_distance
    The result is converted to radians.

    Cone search is preceded by a square search to reduce the search radius before annotating the queryset, in
    order to make the query faster.

    :param queryset: Queryset of Target objects
    :type queryset: Target

    :param ra: Right ascension of center of cone.
    :type ra: float

    :param dec: Declination of center of cone.
    :type dec: float

    :param radius: Radius of cone search in degrees.
    :type radius: float
    """
    ra = np.round(ra,7)
    dec = np.round(dec,7)
    # radius = float(radius)
    double_radius = radius * 2
    queryset = queryset.filter(
        ra__gte=ra - double_radius, ra__lte=ra + double_radius,
        dec__gte=dec - double_radius, dec__lte=dec + double_radius
    )

    separation = ExpressionWrapper(
            180 * ACos(
                (Sin(radians(dec)) * Sin(Radians('dec'))) +
                (Cos(radians(dec)) * Cos(Radians('dec')) * Cos(radians(ra) - Radians('ra')))
            ) / Pi(), FloatField()
        )

    return queryset.annotate(separation=separation).filter(separation__lte=radius)

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
    # double_radius = radius * 2

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

def create_candidate_cutouts(candidate, file_name, product_type):
    file_path = os.path.join(settings.TRANSIENT_DIR+'cutouts', file_name)
    if os.path.exists(file_path):
        with open(file_path, 'rb') as file:
            wrapped_file = File(file)  # Wrap the file object with Django's File class
            wrapped_file.name = os.path.basename(file_path) # Set the file name to the base name of the path
            CandidateDataProduct.objects.create(
                candidate=candidate,
                datafile=wrapped_file,
                data_product_type=product_type,
                name=f'{wrapped_file.name}_{product_type}_cutout'
            )
    else:
        print(f"Error: File {file_name} does not exist.")

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
    if matching_candidates.exists():
        return matching_candidates.exists(), matching_candidates[0]
    # Return True if any matching candidates are found, False otherwise
    return matching_candidates.exists(), None

def fetch_ps1_cutout(ra, dec):
    """
    Fetches a PS1 color composite cutout image for a given RA/Dec.
    :param ra: Right Ascension in degrees.
    :param dec: Declination in degrees.
    """
    files_query_base_url = 'http://ps1images.stsci.edu/cgi-bin/ps1filenames.py'
    params = {
        "ra" : ra,
        "dec" : dec,
    }
    try:
        response = requests.get(files_query_base_url, params=params, timeout=30)
        #print the response
        data = response.text
        # Load data into a pandas DataFrame
        data_lines = data.split('\n')
        header = data_lines[0].split()
        rows = [line.split() for line in data_lines[1:] if line.strip()]

        df = pd.DataFrame(rows, columns=header)

        cutout_base_url = 'https://ps1images.stsci.edu/cgi-bin/fitscut.cgi?'
        url = f'{cutout_base_url}?red={df[df["filter"] == "i"]["filename"].values[0]}&green={df[df["filter"] == "r"]["filename"].values[0]}&blue={df[df["filter"] == "g"]["filename"].values[0]}&x={ra}&y={dec}&size=240&wcs=1&asinh=True&autoscale=99.750000'

        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if response.status_code == 200:
            return ContentFile(BytesIO(response.content).read(), name=f"ps1_cutout.{format}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch PS1 cutout: {e}")
    return None

def fetch_sdss_cutout(ra, dec):
    """
    Fetches an SDSS color composite cutout image for a given RA/Dec.
    :param ra: Right Ascension in degrees.
    :param dec: Declination in degrees.
    """
    url = f'https://skyserver.sdss.org/dr16/SkyServerWS/ImgCutout/getjpeg?ra={ra}&dec={dec}&scale=0.2&width=240&height=240&opt=G'
    print(url)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if response.status_code == 200:
            return ContentFile(BytesIO(response.content).read(), name=f"sdss_cutout.jpg")
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch SDSS cutout: {e}")

def save_alert(candidate,discovery_datetime,filename,last_report = None):
    """
    Save the candidate as an alert in the database.
    :param candidate: Candidate instance
    :param discovery_datetime: Discovery datetime of the alert
    :param filename: Name of the json file
    :param last_report: last report section from the json file
    :return: The alert instance
    """
    #Extract the attributes from the last report
    if last_report != {}:
        mount = last_report.get('mount', {})
        camera = last_report.get('camera', {})
        fieldid = last_report.get('field', {})
        subimage = last_report.get('cropid', {})
        score = last_report.get('score', {})
        ref_cutout_filename = last_report.get('ref_cutout', {})
        new_cutout_filename = last_report.get('new_cutout', {})
        diff_cutout_filename = last_report.get('diff_cutout', {})
    #create the alert
        alert = CandidateAlert.objects.create(
            candidate = candidate,
            filename = os.path.basename(filename),
            discovery_datetime = discovery_datetime,
            mount = mount,
            camera = camera,
            fieldid = fieldid,
            subimage = subimage,
            score = score,
            ref_cutout_filename = ref_cutout_filename,
            new_cutout_filename = new_cutout_filename,
            diff_cutout_filename = diff_cutout_filename,
        )
    #If the last report is empty, ingest the attributes from the filename
    else:
        attributes = filename.split("_")
        mount = attributes[0].split(".")[2]
        camera = attributes[0].split(".")[3]
        fieldid = attributes[3]
        subimage = attributes[6]
    
        #create the alert
        alert = CandidateAlert.objects.create(
            candidate = candidate,
            filename = filename,
            discovery_datetime = discovery_datetime,
            mount = mount,
            camera = camera,
            fieldid = fieldid,
            subimage = subimage,
        )
    return alert

def photometry_exists(candidate, obs_date, magnitude, magnitude_error, filter_band='clear'):
    """
    Check if a photometry entry already exists for a given candidate, observation date, magnitude, and filter.
    """
    obs_date = make_aware(obs_date)
    query = CandidatePhotometry.objects.filter(
        candidate=candidate,
        obs_date__gte=obs_date - timedelta(seconds=5),  # Allowing 5s tolerance
        obs_date__lte=obs_date + timedelta(seconds=5),
        filter_band=filter_band
    )

    # Check for both magnitude and magnitude error if it’s a detection
    if magnitude is not None:
        query = query.filter(magnitude=magnitude, magnitude_error=magnitude_error)
    
    return query.exists()

def add_photometry_from_last_report(candidate,last_report):
    """
    Add photometry data from the json last report.
    :param candidate: Candidate instance
    :param last_report: last report section from the json file
    """
    detections_jd = np.array(last_report.get('detections_jd', {}))
    detections = last_report.get('detections_mag', {})
    detections_magerr = last_report.get('detections_magerr', {})
    nondetections_jd = last_report.get('nondetections_jd', {})
    nondetections_mag = last_report.get('nondetections_mag', {})
    for i, jd in enumerate(detections_jd):
        obs_date = Time(jd,format='jd').to_datetime()
        magnitude = detections[i]
        magnitude_error = detections_magerr[i]
        if not photometry_exists(candidate, obs_date, magnitude, magnitude_error):
            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=obs_date,
                magnitude=magnitude,
                magnitude_error=magnitude_error,
                filter_band='clear',  # Use a mapping if needed to human-readable filter names
                telescope="LAST",
                instrument="LAST-CAM"
            )
        else:
            print("photometry exists")

    for i, jd in enumerate(nondetections_jd):
        obs_date = Time(jd,format='jd')
        magnitude = nondetections_mag[i]
        CandidatePhotometry.objects.create(
            candidate=candidate,
            obs_date=obs_date.iso,
            limit = magnitude,
            filter_band='clear',  # Use a mapping if needed to human-readable filter names
            telescope="LAST",
            instrument="LAST-CAM"
            )

def process_json_file(file):
    """
    Processes the uploaded JSON file and adds candidates to the database.
    :param file: Ingested json file object 
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
    last_report = data.get('last_report', {})
    ra = at_report.get('RA', {}).get('value')
    dec = at_report.get('Dec', {}).get('value')
    discovery_datetime = at_report.get('discovery_datetime',{})[0]
    discovery_datetime = discovery_datetime[:discovery_datetime.find('UTC')-1]
    
    candidate_exists, existing_candidate = check_candidate_exists_by_cone(float(ra), float(dec), radius_arcsec=3)
    if candidate_exists:
        save_alert(existing_candidate,discovery_datetime,file.name,last_report)
        if last_report != {}:
            latest_photometry = CandidatePhotometry.objects.filter(candidate = existing_candidate).order_by('-obs_date').first()
            # start_jd = Time(latest_photometry.obs_date.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"), format='iso').jd
            add_photometry_from_last_report(existing_candidate,last_report)    
            get_atlas_fp(existing_candidate) # Query ATLAS API for additional photometry data
            wrapped_file = File(file)
            wrapped_file.name = os.path.basename(file.name)
            CandidateDataProduct.objects.create(
            candidate=existing_candidate,
            datafile=wrapped_file,
            data_product_type='json',
            name = wrapped_file.name
            )
            #update candidate cutouts
            update_candidate_cutouts(existing_candidate)
        return None

    # Save to the database
    candidate = Candidate.objects.create(ra=ra, dec=dec,discovery_datetime=discovery_datetime)
    save_alert(candidate,discovery_datetime,file.name,last_report)
    #save the json file as a data product
    wrapped_file = File(file)
    wrapped_file.name = os.path.basename(file.name)
    CandidateDataProduct.objects.create(
            candidate=candidate,
            datafile=wrapped_file,
            data_product_type='json',
            name=os.path.basename(file.name)
        )
    candidates_added += 1

    # If there is no last report (old json format), add photometry from the AT tns report
    if last_report == {}:
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
    # If there is a last report, add photometry and cutouts from the last report
    if last_report != {}:
        try:
            add_photometry_from_last_report(candidate,last_report)
            get_atlas_fp(candidate) # Query ATLAS API for additional photometry data
        except Exception as e:
            print(f"Error adding photometry: {e}")

        #cutout images ingestion
        update_candidate_cutouts(candidate)
    
    #PS1 cutout
    try:
        ps1_cutout = fetch_ps1_cutout(ra, dec)
    except Exception as e:
        ps1_cutout = None
    if ps1_cutout:
        CandidateDataProduct.objects.create(
            candidate=candidate,
            datafile=ps1_cutout,
            data_product_type='ps1',
            name=f"{candidate.name} PS1 cutout",
        )
    #SDSS cutout
    try:
        sdss_cutout = fetch_sdss_cutout(ra, dec)
    except Exception as e:
        print(f"Error fetching SDSS cutout for candidate {candidate.id}: {e}")
        sdss_cutout = None
    if sdss_cutout:
        CandidateDataProduct.objects.create(
            candidate=candidate,
            datafile=sdss_cutout,
            data_product_type='sdss',
            name=f"{candidate.name} SDSS cutout",
        )

    return candidates_added

def process_multiple_json_files(directory_path):
    """
    Processes multiple JSON files from a given directory and adds candidates to the database.
    :param directory_path: Path to the directory containing JSON files
    :return: The total number of candidates successfully added
    """
    # json_files = [f for f in os.listdir(directory_path) if f.endswith('.json')]
    last_candidate_alert = CandidateAlert.objects.order_by('-created_at').first()
    process_after_date = last_candidate_alert.created_at if last_candidate_alert else datetime.min

    # computer_time_zone = zoneinfo.ZoneInfo("UTC")
    # Convert process_after_date to a naive Python datetime object (strip timezone), notice the timezone is currently hardcoded to "Asia/Jerusalem"
    if process_after_date.tzinfo is not None:
        process_after_date = process_after_date.replace(tzinfo=None)
    print(process_after_date)
    json_files = [
        file for file in glob.glob(os.path.join(directory_path, "*.json"))
        if datetime.fromtimestamp(os.path.getmtime(file)) > process_after_date
    ]
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
            traceback.print_exc()

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
    matching_targets = cone_search_filter(queryset, candidate.ra, candidate.dec, radius_deg)
    # Return the first matching target if any, or None
    return matching_targets.first()


def transfer_candidate_photometry_to_target(candidate, target):
    """
    Transfers all photometry data from a candidate to a target as ReducedDatum entries.
    :param candidate: Candidate instance
    :param target: Target instance
    """
    # Query all photometry for the candidate
    photometry_entries = candidate.photometry.all()

    for entry in photometry_entries:
        # Prepare the JSON value for ReducedDatum
        print(Time(entry.obs_date.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"), format='iso').mjd)
        value = {
            "time": Time(entry.obs_date.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"), format='iso').mjd,  # Store as mjd
            "filter": entry.filter_band,
            "magnitude": entry.magnitude,
            "error": entry.magnitude_error,
            "limit": entry.limit,
        }

        # Create the ReducedDatum entry
        try:
            ReducedDatum.objects.create(
                target=target,
                data_type="photometry",
                source_name=f"{entry    .telescope}" if entry.telescope and entry.instrument else "Unknown Source",
                timestamp=entry.obs_date,
                value=value,
            )
        except Exception as e:
            # Handle validation errors or duplicates
            print(f"Error saving photometry point: {e}")

def add_candidate_as_target(candidate_id, group_name='LAST general'):
    """
    Adds a candidate as a TOM target if it doesn't already exist and assigns it to a specific group.
    :param candidate_id: The ID of the candidate to add as a target.
    :param group_name: The name of the authentication group to assign the target, default is 'LAST general'.
    :return: The newly created or existing Target object.
    """
    # Check if the target already exists
    existing_target = check_target_exists_for_candidate(candidate_id)
    if existing_target:
        return existing_target  # If the target exists, return it

    # Retrieve the candidate
    candidate = get_object_or_404(Candidate, id=candidate_id)

    # Retrieve or create the group
    
    group, _ = Group.objects.get_or_create(name=group_name)

    # Set the name as the IAU name or the LAST target name if no IAU name
    name = candidate.tns_name if candidate.tns_name else candidate.name
    
    # Create the new target
    target = Target.objects.create(
        name=name,
        type=Target.SIDEREAL,  # Assuming sidereal targets
        ra=candidate.ra,
        dec=candidate.dec,
    )

    #Add the candidate photometry to the target
    transfer_candidate_photometry_to_target(candidate, target)

    # Assign object-level permissions to the group
    assign_perm('tom_targets.view_target', group, target)
    assign_perm('tom_targets.change_target', group, target)
    assign_perm('tom_targets.delete_target', group, target)

    return target

def update_candidate_cutouts(candidate):
    last_alert = CandidateAlert.objects.filter(candidate=candidate).order_by('-created_at').first()
    if last_alert:
        ref_cutout = last_alert.ref_cutout_filename
        new_cutout = last_alert.new_cutout_filename
        diff_cutout = last_alert.diff_cutout_filename
        try:
            if ref_cutout:
                create_candidate_cutouts(candidate, ref_cutout, 'ref')
            if new_cutout:
                create_candidate_cutouts(candidate, new_cutout, 'new')
            if diff_cutout:
                create_candidate_cutouts(candidate, diff_cutout, 'diff')
        except:
            print("Error updating cutouts")
            return None

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

    # Get the current timezone-aware datetime
    today = now()

    # Get unique telescope and filter_band pairs
    telescope_band_pairs = set(
        (telescope, band)
        for telescope in photometry.values_list('telescope', flat=True).distinct()
        for band in photometry.filter(telescope=telescope).values_list('filter_band', flat=True).distinct()
    )

    # Create the Plotly graph
    fig = go.Figure()

    name2color = {'LAST_clear': '#636efa',
                  'ATLAS_o': '#FFA500', 'ATLAS_c': '#2aa198'}
    
    for telescope, filter_band in telescope_band_pairs:
        
        filtered_photometry = photometry.filter(telescope=telescope, filter_band=filter_band)
        
        # Separate detections (magnitude is not empty) and non-detections (magnitude is empty)
        detections = filtered_photometry.exclude(magnitude__isnull=True).order_by('obs_date')
        non_detections = filtered_photometry.filter(magnitude__isnull=True).order_by('obs_date')

        binned_detections = bin_photometry_points(detections)
        binned_non_detections = bin_photometry_points(non_detections)

        
        # Prepare data for original detections
        original_detection_days_ago = [(today - p.obs_date).total_seconds() / (24 * 3600) for p in detections]
        original_detection_magnitudes = [p.magnitude for p in detections]
        original_detection_errors = [p.magnitude_error for p in detections]
        
        # Prepare data for detections
        detection_days_ago = [(today - p.obs_date).total_seconds() / (24 * 3600) for p in binned_detections]
        detection_magnitudes = [p.magnitude for p in binned_detections]
        detection_errors = [p.magnitude_error for p in binned_detections]
     
        # Prepare data for original non-detections
        original_non_detection_days_ago = [(today - p.obs_date).total_seconds() / (24 * 3600) for p in non_detections]
        original_non_detection_limits = [p.limit for p in non_detections]

        # Prepare data for binned non-detections
        non_detection_days_ago = [(today - p.obs_date).total_seconds() / (24 * 3600) for p in binned_non_detections]
        non_detection_limits = [p.limit for p in binned_non_detections]

        # Add detections as scatter points with error bars
        color = name2color.get(f"{telescope}_{filter_band}", 'gray')
        fig.add_trace(go.Scatter(
            x=original_detection_days_ago,
            y=original_detection_magnitudes,
            error_y=dict(
                type='data',
                array=original_detection_errors,
                visible=True,
                color=color_to_rgba(color, 0.2)  # Dynamically adjust error bar opacity
            ),
            mode='markers',
            marker=dict(symbol='circle', size=8, color=color, opacity=0.2),
            name=f"{telescope}_{filter_band} Original Detections",
            legendgroup=f"{telescope}_{filter_band}",
            showlegend=False  # Hide from legend to avoid clutter
        ))

        # Add binned detections as scatter points with error bars
        fig.add_trace(go.Scatter(
            x=detection_days_ago,
            y=detection_magnitudes,
            error_y=dict(
                type='data',
                array=detection_errors,
                visible=True
            ),
            mode='markers',
            marker=dict(symbol='circle', size=8, color=color),
            name=f"{telescope}_{filter_band} Detections",
            legendrank=1 if telescope == 'LAST' else 2  # Ensure LAST first
        ))

        # Add original non-detections with reduced opacity
        fig.add_trace(go.Scatter(
            x=original_non_detection_days_ago,
            y=original_non_detection_limits,
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color=color, opacity=0.2),
            legendgroup=f"{telescope}_{filter_band}",
            showlegend=False  # Hide from legend to avoid clutter
        ))

        # Add binned non-detections as downward arrows
        fig.add_trace(go.Scatter(
            x=non_detection_days_ago,
            y=non_detection_limits,
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color=color),
            name=f"{telescope}_{filter_band} Non-Detections",
            legendrank=1 if telescope == 'LAST' else 2  # Ensure LAST first
        ))

    # Customize layout
    fig.update_layout(
        xaxis_title="Days Ago",
        yaxis_title="Magnitude",
        yaxis=dict(autorange="reversed"),  # Magnitude is brighter for lower values
        xaxis=dict(
            title="Days Ago",
            autorange="reversed",  # Reverse the x-axis
        ),
        legend=dict(
            orientation="v",  # Vertical legend
            x=1,  # Position the legend at the right
            y=0.5,  # Center the legend
        ),
        margin=dict(l=50, r=0, t=10, b=100),  # Tight margins
        paper_bgcolor="rgba(0,0,0,0)",  # Transparent outer background
    )

    # Convert the graph to HTML for embedding in the template
    graph_html = fig.to_html(full_html=False, include_plotlyjs='cdn')

    return mark_safe(graph_html)


def color_to_rgba(color, alpha=1.0):
    """
    Convert a color (hex or named) to an RGBA string with the specified alpha value.
    :param color: Color string (e.g., '#636efa' or 'blue')
    :param alpha: Opacity value (0.0 to 1.0)
    :return: RGBA color string (e.g., 'rgba(99, 110, 250, 0.2)')
    """
    try:
        # Try to convert hex color to RGB
        rgb = hex_to_rgb(color)
    except ValueError:
        # If it's not a hex color, assume it's a named color
        rgb = unlabel_rgb(color)

    # Format as RGBA
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha})"
    

def get_atlas_fp(candidate, days_ago=10):
    """
    Query the ATLAS API for force-photometry data.
    :param candidate: candidate instance
    :param days_ago: Number of days ago for force photometry. Default is 10 days.
    :return: None
    """
    
    atlas_settings = settings.BROKERS.get('ATLAS', {})
    username = atlas_settings.get('user_name')
    password = atlas_settings.get('password')
    if not username or not password:
        print("ATLAS API credentials not set.")
        return None

    try:
        resp = requests.post(url=f"{ATLAS_BASEURL}/api-token-auth/",
                             data={'username': {username}, 'password': {password}})

        if resp.status_code == 200:
            token = resp.json()['token']
            headers = {'Authorization': f'Token {token}', 'Accept': 'application/json'}
        else:
            print(f'ERROR {resp.status_code}')
            print(resp.json())
            
        task_url = None
        while not task_url:
            with requests.Session() as s:
                resp = s.post(f"{ATLAS_BASEURL}/queue/", headers=headers,
                              data={'ra': {str(candidate.ra)}, 'dec': {str(candidate.dec)},
                                    'mjd_min': {Time(now()).mjd-days_ago}, 'send_email': False})

                if resp.status_code == 201:  # successfully queued
                    task_url = resp.json()['url']
                    print(f'The task URL is {task_url}')
                elif resp.status_code == 429:  # throttled
                    message = resp.json()["detail"]
                    print(f'{resp.status_code} {message}')
                    t_sec = re.findall(r'available in (\d+) seconds', message)
                    t_min = re.findall(r'available in (\d+) minutes', message)
                    if t_sec:
                        waittime = int(t_sec[0])
                    elif t_min:
                        waittime = int(t_min[0]) * 60
                    else:
                        waittime = 10
                    print(f'Waiting {waittime} seconds')
                    time.sleep(waittime)
                else:
                    print(f'ERROR {resp.status_code}')
                    print(resp.json())

        result_url = None
        while not result_url:
            with requests.Session() as s:
                resp = s.get(task_url, headers=headers)

                if resp.status_code == 200:  # HTTP OK
                    if resp.json()['finishtimestamp']:
                        result_url = resp.json()['result_url']
                        print(f"Task is complete with results available at {result_url}")
                        break
                    elif resp.json()['starttimestamp']:
                        print(f"Task is running (started at {resp.json()['starttimestamp']})")
                    else:
                        print("Waiting for job to start. Checking again in 10 seconds...")
                    time.sleep(10)
                else:
                    print(f'ERROR {resp.status_code}')
                    print(resp.json())
                    # sys.exit()

        with requests.Session() as s:
            textdata = s.get(result_url, headers=headers).text

        dfresult = pd.read_csv(io.StringIO(textdata.replace("###", "")), delim_whitespace=True)

        SNT = 5.

        for obs in dfresult.iloc:
            obs_date = Time(obs.MJD,format='mjd').to_datetime()
            if obs.uJy/obs.duJy >= SNT:
                magnitude = obs.m  # Detection
                magnitude_error = obs.dm
                limit = None
            else:
                magnitude = None  # Non detection
                limit = obs.mag5sig
                magnitude_error = None
            
            if not photometry_exists(candidate, obs_date, magnitude, magnitude_error):
                CandidatePhotometry.objects.create(
                    candidate=candidate,
                    obs_date=obs_date,
                    magnitude=magnitude,  # Null if non-detection
                    magnitude_error=magnitude_error,
                    filter_band=obs.F,  # Use a mapping if needed to human-readable filter names
                    telescope="ATLAS",
                    instrument=f"ATLAS_{obs.F}",
                    limit=limit
                )
 
    except requests.RequestException as e:
        print(f"Error querying ATLAS API: {e}")
        return None
    
    except Exception as e:
        print(f"Error querying ATLAS API: {e}")
        traceback.print_exc()
        return None


def bin_photometry_points(points, max_time_diff=0.1):
    """
    Bin data points that are less than max_time_diff days apart.
    :param points: List of data points (each with obs_date, magnitude, magnitude_error, etc.)
    :param max_time_diff: Maximum time difference (in days) to bin points together
    :return: Binned data points

    Note: This function assumes that the input points are sorted by obs_date.
    """
    binned_points = []
    current_bin = []

    for i, point in enumerate(points):
        if not current_bin:
            current_bin.append(point)
        else:
            time_diff = (point.obs_date - current_bin[-1].obs_date).total_seconds() / (24 * 3600)
            if time_diff <= max_time_diff:
                current_bin.append(point)
            else:
                # Process the current bin
                binned_points.append(average_photometry_bin(current_bin))
                current_bin = [point]

    # Process the last bin
    if current_bin:
        binned_points.append(average_photometry_bin(current_bin))

    return binned_points


def average_photometry_bin(bin_points):
    """
    Compute the average values for a bin of points.
    :param bin_points: List of points in the bin
    :return: A single averaged point
    """
    avg_obs_date = np.mean([p.obs_date.timestamp() for p in bin_points])
    avg_obs_date = datetime.fromtimestamp(avg_obs_date)
    avg_magnitude = np.mean([p.magnitude for p in bin_points if p.magnitude is not None])
    sum_squared_errors = sum([p.magnitude_error**2 for p in bin_points if p.magnitude_error is not None])
    avg_magnitude_error = np.sqrt(sum_squared_errors) if sum_squared_errors > 0 else None
    avg_limit = np.mean([p.limit for p in bin_points if p.limit is not None])

    # Return a new point with averaged values
    return SimpleNamespace(
        obs_date=make_aware(avg_obs_date),
        magnitude=avg_magnitude,
        magnitude_error=avg_magnitude_error,
        limit=avg_limit
    )



def tns_cone_search(ra, dec, radius=3.0):
    """
    Perform a cone search on the Transient Name Server.

    Parameters:
        ra (float): Right Ascension in degrees.
        dec (float): Declination in degrees.
        radius (float): Search radius in arcsec.

    Returns:
        dict: The response data from the TNS.
    """
    # API endpoint for the cone searchtns_settings = settings.BROKERS.get('TNS', {})
    TNS                 = "www.wis-tns.org"
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
            "units": "arcsec"
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

def convert_datetime_tns(dt_str):
    """
    Converts the json file dates that Ruslan makes to the TNS format.
    """
    t = Time(dt_str.strip(' UTC'), format='iso', scale='utc')
    # Get the fractional day representation
    fractional_day = t.mjd - int(t.mjd) #+ 0.5*random.random()  # Julian date fraction
    date_str = t.iso.split(" ")[0]  # Extract just the date part (YYYY-MM-DD)

    # Combine the date and the fractional day
    fractional_time = f"{date_str}.{str(fractional_day)[2:7]}"

    return fractional_time

def transform_json_tns(input_data):
    """
    Transform the current input JSON data to the desired format for the TNS API.
    The input data currently does not have the correct format for the TNS API (as of Dec 2024).

    Parameters:
        input_data (dict): The input JSON data.

    Returns:
        dict: The transformed JSON data
    """
    
    return {
        "at_report": {
            "0": {
                "ra": {
                    "value": str(input_data["at_report"]["RA"]["value"])
                },
                "dec": {
                    "value": f"{str(input_data['at_report']['Dec']['value'])}"
                },
                "reporting_group_id": input_data["at_report"]["reporting_group_id"],
                "discovery_data_source_id": input_data["at_report"]["discovery_data_source_id"],
                "reporter": input_data["at_report"]["reporter"],
                "discovery_datetime": convert_datetime_tns(input_data["at_report"]["discovery_datetime"][0]),
                "at_type": input_data["at_report"]["at_type"],
                "non_detection": {
                    "obsdate": convert_datetime_tns(input_data["at_report"]["non_detection"]["obsdate"][0]),
                    "limiting_flux": input_data["at_report"]["non_detection"]["flux"],
                    "flux_units": input_data["at_report"]["non_detection"]["flux_units"],
                    "filter_value": input_data["at_report"]["non_detection"]["filter_value"],
                    "instrument_value": input_data["at_report"]["non_detection"]["instrument_value"],
                    "exptime": input_data["at_report"]["non_detection"]["exptime"]
                },
                "photometry": {
                    "photometry_group": {
                        "0": {
                            "obsdate": convert_datetime_tns(input_data["at_report"]["photometry"]["photometry_group"]["obsdate"][0]),
                            "flux": input_data["at_report"]["photometry"]["photometry_group"]["flux"],
                            "limiting_flux": "",
                            "flux_units": input_data["at_report"]["photometry"]["photometry_group"]["flux_units"],
                            "filter_value": input_data["at_report"]["photometry"]["photometry_group"]["filter_value"],
                            "instrument_value": input_data["at_report"]["photometry"]["photometry_group"]["instrument_value"],
                            "exptime": input_data["at_report"]["photometry"]["photometry_group"]["exptime"]
                        }
                    }
                }
            }
        }
    }

def send_json_tns_report(report):
    TNS                 = "www.wis-tns.org"
    url_tns_api         = "https://" + TNS + "/api"
    tns_settings = settings.BROKERS.get('TNS', {})
    TNS_BOT_ID          = tns_settings.get('bot_id')
    TNS_BOT_NAME        = tns_settings.get('bot_name')
    TNS_API_KEY         = tns_settings.get('api_key')
    
    json_url = url_tns_api + "/set/bulk-report"
    tns_marker = 'tns_marker{"tns_id": "' + str(TNS_BOT_ID) + '", "type": "bot", "name": "' + TNS_BOT_NAME + '"}'
    headers = {'User-Agent': tns_marker}
    json_read = json.dumps(report, indent = 4)
    json_data = {'api_key': TNS_API_KEY, 'data': json_read}
    response = requests.post(json_url, headers = headers, data = json_data)
    return response


def send_tns_reply(id_report):
    TNS                 = "www.wis-tns.org"
    url_tns_api         = "https://" + TNS + "/api"
    tns_settings = settings.BROKERS.get('TNS', {})
    TNS_BOT_ID          = tns_settings.get('bot_id')
    TNS_BOT_NAME        = tns_settings.get('bot_name')
    TNS_API_KEY         = tns_settings.get('api_key')
    
    reply_url = url_tns_api + "/get/bulk-report-reply"
    tns_marker = 'tns_marker{"tns_id": "' + str(TNS_BOT_ID) + '", "type": "bot", "name": "' + TNS_BOT_NAME + '"}'
    headers = {'User-Agent': tns_marker}
    reply_data = {'api_key': TNS_API_KEY, 'report_id': id_report}
    response = requests.post(reply_url, headers = headers, data = reply_data,timeout=30)
    return response

def tns_report_details(candidate,first_name,last_name):
    """
    Get the TNS report details for a candidate before sending the details
    :param candidate: Candidate instance
    :return: The TNS report details or None if not found
    """
    # Perform a cone search on the TNS
    dataproduct = CandidateDataProduct.objects.filter(candidate=candidate,data_product_type='json').first()
    file = dataproduct.datafile
    file_content = file.read().decode('utf-8')  # Decode to string
    data = json.loads(file_content)
    #reporter logic here
    reporters = "R. Konno (WIS), E. A. Zimmerman (WIS), A. Horowicz (WIS), S. Garrappa (WIS), E. O. Ofek (WIS), S. Ben-Ami (WIS), D. Polishook (WIS), O. Yaron (WIS), P. Chen (WIS), A. Krassilchtchikov (WIS), Y. M. Shani (WIS), E. Segre (WIS), A. Gal-Yam (WIS), S. Spitzer (WIS), and K. Rybicki (WIS) on behalf of the LAST Collaboration"
    if first_name == 'Eran':
        reporters = reporters.replace(f"{first_name[0]}. O. {last_name} (WIS), ","")
        reporters = f"{first_name[0]}. O. {last_name} (WIS), {reporters}"
    elif first_name == 'Erez':
        reporters = reporters.replace(f"{first_name[0]}. A. {last_name} (WIS), ","")
        reporters = f"{first_name[0]}. A. {last_name} (WIS), {reporters}"
    else:
        reporters = reporters.replace(f"{first_name[0]}. {last_name} (WIS), ","")
        reporters = f"{first_name[0]}. {last_name} (WIS), {reporters}"
    data["at_report"]["reporter"] = reporters
    return data

def send_tns_report(candidate,first_name,last_name):
    """
    Generate a TNS report based on the original json file
    :param candidate: Candidate instance
    :return: response from the TNS
    """
    if candidate.reported_by_LAST:
        return None
    else:
        # dataproduct = CandidateDataProduct.objects.filter(candidate=candidate,data_product_type='json').first()
        # file = dataproduct.datafile
        # file_content = file.read().decode('utf-8')  # Decode to string
        # data = json.loads(file_content)  # Parse the JSON content
        data = tns_report_details(candidate,first_name,last_name)
        transformed_json = json.dumps(transform_json_tns(data))
        report = json.loads(StringIO(transformed_json).read(), object_pairs_hook = OrderedDict)
        response = send_json_tns_report(report)
        if response.status_code == 200:
            json_response = response.json()
            print(json_response)
            report_id = json_response['data']['report_id']
            time.sleep(5)
            response = send_tns_reply(report_id)
            print(response.json())
            if response.status_code == 400:
                feedback = json.dumps(response.json()['data']['feedback'], indent = 4)
                CandidateDataProduct.objects.create(
                candidate=candidate,
                datafile=ContentFile(feedback),
                data_product_type='tns',
                name=f'failed_tns_{report_id}.json'
                )
                print("hello")
            if response.status_code == 200:
                feedback_data = response.json()['data']['feedback']
                objname = feedback_data['at_report'][0].get('101', {}).get('objname', 'No objname found')
                if objname == 'No objname found':
                    objname = feedback_data['at_report'][0].get('100', {}).get('objname', 'No objname found')
                feedback = json.dumps(response.json()['data']['feedback'], indent = 4)
                #Save the feedback
                CandidateDataProduct.objects.create(
                candidate=candidate,
                datafile=ContentFile(feedback),
                data_product_type='tns',
                name=f'tns_{report_id}.json'
                )
                candidate.tns_name = objname
                candidate.reported_by_LAST = True
                try:
                    candidate.save()
                except Exception as e:
                    print(f"Error saving candidate: {e}")
                print(candidate.tns_name)

def get_horizons_data(candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    ra = candidate.ra
    dec = candidate.dec
    obstime = candidate.discovery_datetime.strftime('%Y-%m-%d_%H:%M:%S')
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    ra_hms = coord.ra.to_string(unit=u.hour, sep='-', precision=2, pad=True)  # RA in hh:mm:ss.ss
    dec_dms = coord.dec.to_string(unit=u.degree, sep='-', precision=2, alwayssign=True, pad=True).strip("+")  # Dec in dd:mm:ss.ss
    # API endpoint
    base_url = "https://ssd-api.jpl.nasa.gov/sb_ident.api"

    # Define parameters for the query
    params = {
        # "mpc-code": "097",  # Wise Observatory (MPC code)
        "lat": "30.053169", # Latitude of the observatory in Neot Smadar (degrees)
        "lon": "35.041526",  # Longitude of the observatory in Neot Smadar(degrees)
        "alt": "0.405", # Altitude of the observatory in Neot Smadar(km)
        "obs-time": obstime,  # Observation time (UTC format)
        "fov-ra-center": ra_hms,  # RA of the center of the field of view (hh-mm-ss.ss)
        "fov-dec-center": dec_dms,  # Dec of the center of the field of view (dd-mm-ss.ss)
        "fov-ra-hwidth": 0.27778,  # Half-width of the field of view in RA (degrees)
        "fov-dec-hwidth": 0.27778,  # Half-width of the field of view in Dec (degrees)
        "two-pass": True,  # Use high-precision numerical integration
        "mag-required": True,  # Require magnitude data
        "vmag-lim": 22.0,  # Visual magnitude threshold
        "req-elem": False,  # Do not request orbital elements
    }

    # Make the API request
    response = requests.get(base_url, params=params)
    data = response.json()
    if data['data_first_pass']:
        return data
    else:
        return None