
import logging
import os
from io import BytesIO
from math import radians


import astropy.units as u
import numpy as np
import pandas as pd
import requests
from astropy.coordinates import SkyCoord
from astropy.time import Time

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files import File
from django.core.files.base import ContentFile
from django.db.models import ExpressionWrapper, FloatField, Value
from django.db.models.functions import ACos, Cos, Greatest, Least, Pi, Radians, Sin
from django.shortcuts import get_object_or_404
from guardian.shortcuts import assign_perm
from tom_dataproducts.models import ReducedDatum
from tom_targets.models import Target

from .constants import TNS_REPORT_GROUPS
from .models import Candidate, CandidateDataProduct

logger = logging.getLogger(__name__)

CAST_SETTINGS = settings.CAST_CANDIDATES


def can_send_tns_report(user):
    """
    Whether the given user is allowed to send TNS reports / perform LAST general
    candidate actions.

    Membership of any of the `TNS_REPORT_GROUPS` auth groups grants access;
    superusers are always allowed. This is the single source of truth shared by the
    views and the templates so the UI and the server-side gate can never drift apart.
    """
    return bool(
        getattr(user, 'is_authenticated', False)
        and (user.is_superuser or user.groups.filter(name__in=TNS_REPORT_GROUPS).exists())
    )

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
    ra = np.round(ra, 7)
    dec = np.round(dec, 7)
    # radius = float(radius)
    double_radius = radius * 2
    queryset = queryset.filter(
        ra__gte=ra - double_radius, ra__lte=ra + double_radius,
        dec__gte=dec - double_radius, dec__lte=dec + double_radius
    )

    # Clamp the cosine term to [-1, 1] before ACos: floating-point rounding can push
    # it slightly outside that range for (near-)identical coordinates, which makes the
    # DB's acos() error out or return NULL — exactly the exact-duplicate dedup case.
    cos_separation = (
        (Sin(radians(dec)) * Sin(Radians('dec'))) +
        (Cos(radians(dec)) * Cos(Radians('dec')) * Cos(radians(ra) - Radians('ra')))
    )
    separation = ExpressionWrapper(
        180 * ACos(Least(Value(1.0), Greatest(Value(-1.0), cos_separation))) / Pi(),
        FloatField()
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
    # Clamp the cosine term to [-1, 1] before ACos: floating-point rounding can push
    # it slightly outside that range for (near-)identical coordinates, which makes the
    # DB's acos() error out or return NULL — exactly the exact-duplicate dedup case.
    cos_separation = (
        (Sin(radians(dec)) * Sin(Radians('dec'))) +
        (Cos(radians(dec)) * Cos(Radians('dec')) * Cos(radians(ra) - Radians('ra')))
    )
    separation = ExpressionWrapper(
        180 * ACos(Least(Value(1.0), Greatest(Value(-1.0), cos_separation))) / Pi(),
        FloatField()
    )

    # Annotate queryset with separation and filter by the radius
    return queryset.annotate(separation=separation).filter(separation__lte=radius)


def create_candidate_cutouts(candidate, file_name, product_type):
    file_path = os.path.join(settings.TRANSIENT_DIR, "cutouts", file_name)
    if not os.path.exists(file_path):
        logger.error(f"Error: File {file_name} does not exist.")
        return


    with open(file_path, 'rb') as file:
        wrapped_file = File(file)  # Wrap the file object with Django's File class
        wrapped_file.name = os.path.basename(file_path)  # Set the file name to the base name of the path
        CandidateDataProduct.objects.create(
            candidate=candidate,
            datafile=wrapped_file,
            data_product_type=product_type,
            name=f'{wrapped_file.name}_{product_type}_cutout'
        )



def fetch_ps1_cutout(ra, dec):
    """
    Fetches a PS1 color composite cutout image for a given RA/Dec.
    :param ra: Right Ascension in degrees.
    :param dec: Declination in degrees.
    """
    cfg = CAST_SETTINGS["ps1"]

    params = {"ra": ra, "dec": dec}
    try:
        response = requests.get(
            cfg["files_url"],
            params=params,
            timeout=cfg["timeout"],
        )
        data = response.text

        data_lines = data.split('\n')
        header = data_lines[0].split()
        rows = [line.split() for line in data_lines[1:] if line.strip()]
        df = pd.DataFrame(rows, columns=header)

        url = (
            f'{cfg["cutout_url"]}'
            f'?red={df[df["filter"] == "i"]["filename"].values[0]}'
            f'&green={df[df["filter"] == "r"]["filename"].values[0]}'
            f'&blue={df[df["filter"] == "g"]["filename"].values[0]}'
            f'&x={ra}&y={dec}'
            f'&size={cfg["image_size"]}'
            f'&wcs=1&asinh=True'
            f'&autoscale={cfg["autoscale"]}'
        )

        response = requests.get(url, timeout=cfg["timeout"])
        response.raise_for_status()

        return ContentFile(
            BytesIO(response.content).read(),
            name="ps1_cutout.jpg",
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch PS1 cutout: {e}")
        return None


def fetch_sdss_cutout(ra, dec):
    cfg = CAST_SETTINGS["sdss"]

    params = {
        "ra": ra,
        "dec": dec,
        "scale": cfg["scale"],
        "width": cfg["width"],
        "height": cfg["height"],
        "opt": "G",
    }

    try:
        response = requests.get(
            cfg["cutout_url"],
            params=params,
            timeout=cfg["timeout"],
        )
        response.raise_for_status()

        return ContentFile(
            BytesIO(response.content).read(),
            name="sdss_cutout.jpg",
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch SDSS cutout: {e}")
        return None

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


def check_candidate_exists_for_target(target, radius_arcsec=3):
    """
    Checks if a candidate exists for a given target using a cone search.
    :param target: Target instance.
    :param radius_arcsec: Radius of the cone search in arcseconds (default is 3").
    :return: The first matching Candidate object or None if no match is found.
    """
    radius_deg = radius_arcsec / 3600.0
    queryset = Candidate.objects.all()
    matching_candidates = cone_search_filter_candidates(queryset, target.ra, target.dec, radius_deg)
    return matching_candidates.first()


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
        time = Time(entry.obs_date.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"), format='iso').mjd
        logger.debug(time)
        value = {
            "time": time,
            # Store as mjd
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
                source_name=f"{entry.telescope}" if entry.telescope and entry.instrument else "Unknown Source",
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

    # Add the candidate photometry to the target
    transfer_candidate_photometry_to_target(candidate, target)

    # Assign object-level permissions to the group
    assign_perm('tom_targets.view_target', group, target)
    assign_perm('tom_targets.change_target', group, target)
    assign_perm('tom_targets.delete_target', group, target)

    return target


## todo async..
def get_horizons_data(candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    ra = candidate.ra
    dec = candidate.dec
    obstime = candidate.discovery_datetime.strftime('%Y-%m-%d_%H:%M:%S')
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    ra_hms = coord.ra.to_string(unit=u.hour, sep='-', precision=2, pad=True)  # RA in hh:mm:ss.ss
    dec_dms = coord.dec.to_string(unit=u.degree, sep='-', precision=2, alwayssign=True, pad=True).strip("+")  # Dec in dd:mm:ss.ss

    cfg = CAST_SETTINGS
    params = {
        # "mpc-code": "097",  # Wise Observatory (MPC code)
        "lat": cfg["observatory"]["lat"],  # Latitude of the observatory in Neot Smadar (degrees)
        "lon": cfg["observatory"]["lon"], # Longitude of the observatory in Neot Smadar(degrees)
        "alt": cfg["observatory"]["alt"], # Altitude of the observatory in Neot Smadar(km)
        "obs-time": obstime, # Observation time (UTC format)
        "fov-ra-center": ra_hms, # RA of the center of the field of view (hh-mm-ss.ss)
        "fov-dec-center": dec_dms, # Dec of the center of the field of view (dd-mm-ss.ss)
        "fov-ra-hwidth": cfg["horizons"]["fov_ra_hwidth"],  # Half-width of the field of view in RA (degrees)
        "fov-dec-hwidth": cfg["horizons"]["fov_dec_hwidth"],  # Half-width of the field of view in Dec (degrees)
        "two-pass": cfg["horizons"]["two_pass"],  # Use high-precision numerical integration
        "mag-required": cfg["horizons"]["mag_required"],  # Require magnitude data
        "vmag-lim": cfg["horizons"]["vmag_lim"],  # Visual magnitude threshold
        "req-elem": cfg["horizons"]["req_elem"],  # Do not request orbital elements
    }
    base_url = cfg["horizons"]["base_url"]

    # Make the API request
    response = requests.get(base_url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    if data.get("data_first_pass"):
        return data
    return None
