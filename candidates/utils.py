from .models import Candidate
from tom_targets.models import Target
from tom_targets.utils import cone_search_filter
from django.shortcuts import get_object_or_404
from django.db.models import ExpressionWrapper, FloatField
from django.db.models.functions import ACos, Cos, Radians, Pi, Sin
from math import radians
import os
import json

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
        Candidate.objects.create(ra=ra, dec=dec,discovery_datetime=discovery_datetime)
        candidates_added += 1

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

# def perform_cone_search(ra, dec, radius_arcsec=3):
#     """
#     Perform a cone search using the TOM Toolkit's `cone_search_filter`.
#     :param ra: Right Ascension (in degrees) of the search center.
#     :param dec: Declination (in degrees) of the search center.
#     :param radius_arcsec: Radius of the cone in arcseconds (default is 3 arcseconds).
#     :return: QuerySet of matching Target objects.
#     """
#     # Convert radius from arcseconds to degrees
#     radius_deg = radius_arcsec / 3600.0

#     # Apply the cone search filter to the Target queryset
#     queryset = Target.objects.all()
#     matching_targets = cone_search_filter(queryset, ra, dec, radius_deg)

#     return matching_targets