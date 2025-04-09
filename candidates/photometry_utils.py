# Built-in imports
import io
import re
import requests
import time
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace

# Third-party imports
import numpy as np
import pandas as pd
import plotly.graph_objs as go
from astropy.time import Time
from lasair import lasair_client
from plotly.colors import hex_to_rgb

# Django imports
from django.conf import settings
from django.utils.safestring import mark_safe
from django.utils.timezone import make_aware, now

# Local imports
from .models import CandidatePhotometry


ATLAS_BASEURL = "https://fallingstar-data.com/forcedphot"
LASAIR_ENDPOINT = "https://lasair-ztf.lsst.ac.uk/api"
LASAIR_CONE_RADIUS = 5.0  # arcseconds


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

        dfresult = pd.read_csv(io.StringIO(textdata.replace("###", "")), sep=r"\s+")

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
            
            if not photometry_exists(candidate, obs_date, magnitude,
                                     magnitude_error, filter_band=obs.F):
                CandidatePhotometry.objects.create(
                    candidate=candidate,
                    obs_date=make_aware(obs_date),
                    magnitude=magnitude,  # Null if non-detection
                    magnitude_error=magnitude_error,
                    filter_band=obs.F,  # Use a mapping if needed to human-readable filter names
                    telescope="ATLAS",
                    limit=limit
                )
 
    except requests.RequestException as e:
        print(f"Error querying ATLAS API: {e}")
        return None
    
    except Exception as e:
        print(f"Error querying ATLAS API: {e}")
        traceback.print_exc()
        return None


def get_ztf_fp(candidate, days_ago=10):
    """
    Get the ZTF forced photometry for a candidate.
    """

    lasair_settings = settings.BROKERS.get('LASAIR', {})
    api_token = lasair_settings.get('api_token')
    if not api_token:
        print("LASAIR API credentials not set.")
        raise ValueError("LASAIR API credentials not set.")
    L = lasair_client(lasair_settings['api_token'], endpoint=LASAIR_ENDPOINT)

    result = L.cone(ra=candidate.ra, dec=candidate.dec,
                    radius=LASAIR_CONE_RADIUS, requestType='nearest')
    if 'object' in result:
        print(f"Found {result['object']} at separation {result['separation']:.2f} with radius {LASAIR_CONE_RADIUS}")
    else:
        print('No object found at radius %f' % LASAIR_CONE_RADIUS)
        return None
    
    object_id = result['object']
    lcs = L.lightcurves([object_id])
    dfresult = pd.DataFrame(lcs[0]['candidates']) 
    dfresult = dfresult[dfresult['jd'] > Time.now().jd - days_ago]

    for obs in dfresult.iloc:
        obs_date = Time(obs.jd,format='jd').to_datetime()
        filter_band = 'g' if obs.fid ==1 else 'r'  # fid=1 green, fid=2 red
        if not np.isnan(obs.candid):
            magnitude = obs.magpsf  # Detection
            magnitude_error = obs.sigmapsf
            limit = None
        else:
            magnitude = None  # Non detection
            limit = obs.diffmaglim
            magnitude_error = None
        
        if not photometry_exists(candidate, obs_date, magnitude,
                                 magnitude_error, filter_band=filter_band):
            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=make_aware(obs_date),
                magnitude=magnitude,  # Null if non-detection
                magnitude_error=magnitude_error,
                filter_band=filter_band,
                telescope="ZTF",
                limit=limit
            )


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
                  'ATLAS_o': '#FFA500', 'ATLAS_c': '#2aa198',
                  'ZTF_g': '#008000', 'ZTF_r': '#FF0000',}
    
    tel2rank = {'LAST': 1, 'ZTF': 2, 'ATLAS': 3}
    
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
        color = name2color.get(f"{telescope}_{filter_band}", '#77807f') # some shade of grey
        fig.add_trace(go.Scatter(
            x=original_detection_days_ago,
            y=original_detection_magnitudes,
            error_y=dict(
                type='data',
                array=original_detection_errors,
                visible=True,
                color=rgb_to_rgba(color, 0.3)  # make binned detections transparent
            ),
            mode='markers',
            marker=dict(symbol='circle', size=8, color=color, opacity=0.3),
            name=f"{telescope}_{filter_band} Original Detections",
            legendgroup=f"{telescope}_{filter_band}_detections",
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
            legendgroup=f"{telescope}_{filter_band}_detections",
            legendrank=tel2rank[telescope]
        ))

        # Add original non-detections with reduced opacity
        fig.add_trace(go.Scatter(
            x=original_non_detection_days_ago,
            y=original_non_detection_limits,
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color=color, opacity=0.2),
            legendgroup=f"{telescope}_{filter_band}_non_detections",
            showlegend=False
        ))

        # Add binned non-detections as downward arrows
        fig.add_trace(go.Scatter(
            x=non_detection_days_ago,
            y=non_detection_limits,
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color=color),
            name=f"{telescope}_{filter_band} Non-Detections",
            legendgroup=f"{telescope}_{filter_band}_non_detections",
            legendrank=tel2rank[telescope]
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


def rgb_to_rgba(color, alpha=1.0):
    """
    Convert a color (hex) to an RGBA string with the specified alpha value.
    :param color: Color string (e.g., '#636efa')
    :param alpha: Opacity value (0.0 to 1.0)
    :return: RGBA color string (e.g., 'rgba(99, 110, 250, 0.2)')
    """
    rgb = hex_to_rgb(color)
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha})"
    

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
