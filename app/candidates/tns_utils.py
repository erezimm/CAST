import json
import logging
import os
import time
from collections import OrderedDict
from io import StringIO
from typing import Dict, Optional

import requests
from astropy.time import Time
from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# TNS reporting configuration
TNS_REPLY_WAIT_TIME = 5  # seconds to wait before checking report status

# Reporter list and name-formatting rules live in a JSON file so they can be edited
# without touching the code. Override the location with settings.TNS_REPORTERS_CONFIG_PATH.
DEFAULT_TNS_REPORTERS_PATH = os.path.join(os.path.dirname(__file__), 'config', 'tns_reporters.json')


def load_reporter_config() -> Dict:
    """
    Load the TNS reporter configuration from JSON.

    Returns a dict with the base reporter list, affiliation/closing strings, and the
    special name-formatting cases. Falls back to an empty config (logging the error)
    if the file is missing or invalid, so reporting degrades rather than crashing.
    """
    path = getattr(settings, 'TNS_REPORTERS_CONFIG_PATH', None) or DEFAULT_TNS_REPORTERS_PATH
    try:
        with open(path, 'r', encoding='utf-8') as config_file:
            return json.load(config_file)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load TNS reporter config from {path}: {e}")
        return {}


def _build_base_reporters(config: Dict) -> str:
    """Build the formatted base reporter string from the loaded config."""
    affiliation = config.get('affiliation', '(WIS)')
    closing = config.get('closing', 'on behalf of the LAST Collaboration')
    affiliated = [f"{name} {affiliation}".strip() for name in config.get('base_reporters', [])]

    if not affiliated:
        return closing
    if len(affiliated) == 1:
        return f"{affiliated[0]} {closing}".strip()

    *leading, last = affiliated
    return f"{', '.join(leading)}, and {last} {closing}".strip()


def tns_cone_search(ra: float, dec: float, radius: float = 3.0) -> Optional[Dict]:
    """
    Perform a cone search on the Transient Name Server.

    Parameters:
        ra (float): Right Ascension in degrees.
        dec (float): Declination in degrees.
        radius (float): Search radius in arcsec.

    Returns:
        dict: The response data from the TNS.
    """
    config = get_tns_config()
    endpoint = f"{config['url_api']}/get/search"
    headers = config['headers']

    # Parameters for the cone search
    payload = {
        "api_key": config['api_key'],
        "data": json.dumps({
            "ra": str(ra),
            "dec": str(dec),
            "radius": str(radius),
            "units": "arcsec"
        })
    }
    # Perform the request
    try:
        response = requests.post(endpoint, headers=headers, data=payload, timeout=10)
        response.raise_for_status()  # Raise an error for bad status codes
        return response.json()  # Parse the JSON response
    except requests.RequestException as e:
        logger.error(f"Error during TNS cone search: {e}")
        return None

def julian_date_fraction(t: Time) -> float:
    """
    Get the fractional day representation
    + 0.5*random.random()  # Julian date fraction
    """
    return t.mjd - int(t.mjd)

def convert_datetime_tns(dt_str: str) -> str:
    """
    Convert a datetime string to TNS format.

    Converts datetime strings from JSON files to the fractional day format
    required by the TNS API (YYYY-MM-DD.fractional_day).

    Args:
        dt_str (str): Datetime string with ' UTC' suffix (e.g., "2024-01-01 12:00:00 UTC")

    Returns:
        str: Datetime in TNS fractional day format (e.g., "2024-01-01.5")
    """
    t = Time(dt_str.strip(' UTC'), format='iso', scale='utc')
    # Get the fractional day representation
    fractional_day = julian_date_fraction(t)
    date_str = t.iso.split(" ")[0]  # Extract just the date part (YYYY-MM-DD)

    # Combine the date and the fractional day
    fractional_time = f"{date_str}.{str(fractional_day)[2:7]}"

    return fractional_time


def transform_json_tns(input_data: Dict) -> Dict:
    """
    Transform input JSON data to TNS API format.

    Converts the internal JSON structure to the specific format required
    by the TNS API for astronomical transient reports (as of Dec 2024).

    Args:
        input_data (dict): The input JSON data with at_report structure

    Returns:
        dict: Transformed JSON data in TNS API format with proper datetime conversion
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
                "remarks": input_data["at_report"]["remarks"],
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
                            "obsdate": convert_datetime_tns(
                                input_data["at_report"]["photometry"]["photometry_group"]["obsdate"][0]),
                            "flux": input_data["at_report"]["photometry"]["photometry_group"]["flux"],
                            "limiting_flux": "",
                            "flux_units": input_data["at_report"]["photometry"]["photometry_group"]["flux_units"],
                            "filter_value": input_data["at_report"]["photometry"]["photometry_group"]["filter_value"],
                            "instrument_value": input_data["at_report"]["photometry"]["photometry_group"][
                                "instrument_value"],
                            "exptime": input_data["at_report"]["photometry"]["photometry_group"]["exptime"]
                        }
                    }
                }
            }
        }
    }


def get_tns_config() -> Dict:
    """
    Get TNS API configuration.

    Retrieves and constructs the configuration needed for TNS API calls,
    including URLs, authentication headers, and API keys.

    Returns:
        dict: Configuration dictionary containing:
            - url_api (str): Base TNS API URL
            - bot_id (str): TNS bot ID
            - bot_name (str): TNS bot name
            - api_key (str): TNS API key
            - headers (dict): HTTP headers with User-Agent for TNS API
    """
    tns_settings = settings.BROKERS.get('TNS', {})
    url_base = "https://sandbox.wis-tns.org" if settings.TNS_TEST else "https://www.wis-tns.org"

    return {
        'url_api': f"{url_base}/api",
        'bot_id': tns_settings.get('bot_id'),
        'bot_name': tns_settings.get('bot_name'),
        'api_key': tns_settings.get('api_key'),
        'headers': {
            'User-Agent': f'tns_marker{{"tns_id": "{tns_settings.get("bot_id")}", "type": "bot", "name": "{tns_settings.get("bot_name")}"}}'
        }
    }


def send_json_tns_report(report: Dict) -> requests.Response:
    """
    Send a bulk report to the TNS API.

    Submits an astronomical transient report to the Transient Name Server
    using their bulk reporting endpoint.

    Args:
        report (dict): The formatted report data to send to TNS

    Returns:
        requests.Response: The HTTP response from the TNS API
    """
    config = get_tns_config()
    json_url = config['url_api'] + "/set/bulk-report"
    json_data = {'api_key': config['api_key'], 'data': json.dumps(report, indent=4)}
    response = requests.post(json_url, headers=config['headers'], data=json_data)
    return response


def send_tns_reply(id_report: str) -> requests.Response:
    """
    Get the reply for a submitted TNS bulk report.

    Retrieves the processing results and feedback for a previously submitted
    astronomical transient report from the TNS API.

    Args:
        id_report (str): The report ID returned from the initial bulk report submission

    Returns:
        requests.Response: The HTTP response containing report processing feedback
    """
    config = get_tns_config()
    reply_url = config['url_api'] + "/get/bulk-report-reply"
    reply_data = {'api_key': config['api_key'], 'report_id': id_report}
    response = requests.post(reply_url, headers=config['headers'], data=reply_data, timeout=30)
    return response


def format_reporter_name(first_name: str, last_name: str) -> str:
    """
    Format reporter name for TNS attribution.

    Takes a reporter's first and last name and formats it according to TNS
    standards, placing it at the beginning of the LAST collaboration reporter list.

    Args:
        first_name (str): Reporter's first name
        last_name (str): Reporter's last name

    Returns:
        str: Formatted reporter string with the specified reporter first
    """
    config = load_reporter_config()
    affiliation = config.get('affiliation', '(WIS)')
    special_middle_initials = config.get('special_name_middle_initials', {})
    base_reporters = _build_base_reporters(config)

    first_name = (first_name or '').strip()
    last_name = (last_name or '').strip()

    # Handle special name formatting cases (e.g. people with a known middle initial)
    if first_name in special_middle_initials and last_name:
        middle = special_middle_initials[first_name]
        reporter_name = f"{first_name[0]}. {middle}. {last_name} {affiliation}"
    elif first_name and last_name:
        reporter_name = f"{first_name[0]}. {last_name} {affiliation}"
    elif last_name:
        reporter_name = f"{last_name} {affiliation}"
    elif first_name:
        reporter_name = f"{first_name} {affiliation}"
    else:
        # No reporter name available; fall back to the collaboration list only.
        return base_reporters

    # Remove existing instance if present and prepend
    base_reporters = base_reporters.replace(f"{reporter_name}, ", "")
    return f"{reporter_name}, {base_reporters}"


def extract_tns_object_name(feedback_data: Dict) -> str:
    """
    Extract TNS object name from feedback response.

    Parses the TNS feedback data to find the assigned object name.
    Tries multiple possible keys in the response structure.

    Args:
        feedback_data (dict): The feedback data from TNS API response

    Returns:
        str: The TNS object name, or 'No objname found' if not found
    """
    try:
        at_report = feedback_data.get('at_report', [])
        if not at_report or not isinstance(at_report, list):
            return 'No objname found'

        report_data = at_report[0] if at_report else {}

        # Try different possible keys for the object name
        objname = report_data.get('101', {}).get('objname')
        if not objname:
            objname = report_data.get('100', {}).get('objname')

        return objname or 'No objname found'

    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"Error extracting TNS object name from feedback: {e}")
        return 'No objname found'


def get_tns_object_details(objname: str) -> Optional[Dict]:
    """
    Get detailed information for a specific TNS object.

    Queries the TNS API for detailed information about a specific object,
    including photometry data to check if it was reported by LAST.

    Args:
        objname (str): The TNS object name to query

    Returns:
        dict: Object details including photometry data, or None if not found/error
    """
    config = get_tns_config()
    endpoint = f"{config['url_api']}/get/object"

    payload = {
        "api_key": config['api_key'],
        "data": json.dumps({
            "objname": str(objname),
            "photometry": "1",
            "spectra": "0"
        })
    }

    try:
        response = requests.post(endpoint, headers=config['headers'], data=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result.get('data')
    except requests.RequestException as e:
        logger.error(f"Error fetching TNS object details for {objname}: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing TNS response for {objname}: {e}")
        return None


def check_if_reported_by_last(objname: str) -> tuple[bool, Optional[str]]:
    """
    Check if a TNS object was reported by LAST telescope.

    Queries the object's photometry data to see if any observations
    were made with LAST-Cam instrument.

    Args:
        objname (str): The TNS object name to check

    Returns:
        tuple: (was_reported_by_last: bool, objname: str or None)
    """
    object_data = get_tns_object_details(objname)
    if not object_data:
        return False, None

    photometry = object_data.get('photometry', [])
    for photo_point in photometry:
        instrument_name = photo_point.get('instrument', {}).get('name')
        if instrument_name == 'LAST-Cam':
            return True, objname

    return False, objname


def tns_report_details(candidate, first_name: str, last_name: str, at_type: Optional[str] = None, comment: Optional[str] = None) -> Dict:
    """
    Prepare TNS report details for a candidate.

    Loads the original JSON data for a candidate, modifies it with reporter
    information and optional parameters, and returns the prepared data for
    TNS submission.

    Args:
        candidate (Candidate): The candidate instance to prepare report for
        first_name (str): Reporter's first name for attribution
        last_name (str): Reporter's last name for attribution
        at_type (str, optional): Astronomical transient type
        comment (str, optional): Additional remarks for the report

    Returns:
        dict: Modified JSON data ready for TNS submission
    """
    # Import here to avoid circular import
    from .models import CandidateDataProduct

    # Load and parse the JSON data for the candidate
    dataproduct = CandidateDataProduct.objects.filter(candidate=candidate, data_product_type='json').first()
    file = dataproduct.datafile
    file_content = file.read().decode('utf-8')  # Decode to string
    data = json.loads(file_content)

    # Set the reporter attribution
    data["at_report"]["reporter"] = format_reporter_name(first_name, last_name)
    if comment:
        data["at_report"]["remarks"] = comment
    else:
        data["at_report"]["remarks"] = ""
    data["at_report"]["at_type"] = at_type
    return data


def _submit_tns_report(candidate, report_data: Dict) -> str:
    """
    Submit a TNS report and return the report ID.

    Args:
        candidate (Candidate): The candidate being reported
        report_data (dict): The prepared report data

    Returns:
        str: The TNS report ID

    Raises:
        requests.RequestException: If the submission fails
        KeyError: If the response doesn't contain expected data
    """
    logger.info(f"Submitting TNS report for candidate {candidate.id}")

    # Transform and send the report
    transformed_json = json.dumps(transform_json_tns(report_data))
    report = json.loads(StringIO(transformed_json).read(), object_pairs_hook=OrderedDict)
    response = send_json_tns_report(report)
    response.raise_for_status()

    # Extract report ID from response
    json_response = response.json()
    logger.debug(f"TNS submission response: {json_response}")
    report_id = json_response['data']['report_id']
    logger.info(f"TNS report submitted successfully, report_id: {report_id}")

    return report_id


def _wait_for_tns_reply(report_id: str) -> Dict:
    """
    Wait for TNS processing and retrieve the reply.

    Args:
        report_id (str): The TNS report ID to check

    Returns:
        dict: The feedback data from TNS

    Raises:
        requests.RequestException: If the reply request fails
    """
    logger.debug(f"Waiting {TNS_REPLY_WAIT_TIME} seconds for TNS processing of report {report_id}")
    time.sleep(TNS_REPLY_WAIT_TIME)
    response = send_tns_reply(report_id)

    if response.status_code == 400:
        # Report failed - return the error feedback
        error_data = response.json()['data']['feedback']
        logger.warning(f"TNS report {report_id} failed with status 400")
        return {'status': 'failed', 'feedback': error_data}

    response.raise_for_status()
    feedback_data = response.json()['data']['feedback']
    logger.debug(f"TNS feedback received for report {report_id}")
    return {'status': 'success', 'feedback': feedback_data}


def _process_tns_feedback(candidate, report_id: str, feedback_result: Dict, first_name: str, last_name: str) -> None:
    """
    Process TNS feedback and update the candidate record.

    Args:
        candidate (Candidate): The candidate to update
        report_id (str): The TNS report ID
        feedback_result (dict): The feedback data with status and feedback
        first_name (str): Reporter's first name
        last_name (str): Reporter's last name
    """
    # Import here to avoid circular import
    from .models import CandidateDataProduct

    if feedback_result['status'] == 'failed':
        # Save failed report feedback
        feedback_json = json.dumps(feedback_result['feedback'], indent=4)
        CandidateDataProduct.objects.create(
            candidate=candidate,
            datafile=ContentFile(feedback_json.encode('utf-8'), name=f'failed_tns_{report_id}.json'),
            data_product_type='tns',
            name=f'failed_tns_{report_id}.json'
        )
        logger.error(f"TNS report {report_id} failed, feedback saved for candidate {candidate.id}")
        return

    # Process successful feedback
    feedback_data = feedback_result['feedback']
    objname = extract_tns_object_name(feedback_data)

    # Save successful feedback as data product
    feedback_json = json.dumps(feedback_data, indent=4)
    CandidateDataProduct.objects.create(
        candidate=candidate,
        datafile=ContentFile(feedback_json.encode('utf-8'), name=f'tns_{report_id}.json'),
        data_product_type='tns',
        name=f'tns_{report_id}.json'
    )

    # Update candidate with TNS information
    candidate.tns_name = objname
    candidate.reported_by_LAST = True
    candidate.real_bogus = True
    candidate.real_bogus_user = f"{first_name} {last_name}"

    candidate.save()
    logger.info(f"Candidate {candidate.id} successfully reported to TNS as {objname}")


def send_tns_report(candidate, first_name: str, last_name: str, at_type: Optional[str] = None, comment: Optional[str] = None) -> None:
    """
    Send a complete TNS report for a candidate.

    Orchestrates the full TNS reporting process: prepares the report data,
    submits it to TNS, waits for processing, retrieves feedback, and updates
    the candidate record with the assigned TNS name.

    Args:
        candidate (Candidate): The candidate to report to TNS
        first_name (str): Reporter's first name
        last_name (str): Reporter's last name
        at_type (str, optional): Astronomical transient classification
        comment (str, optional): Additional remarks

    Returns:
        None: Updates candidate in-place and saves feedback as data products
    """
    if candidate.reported_by_LAST:
        logger.info(f"Candidate {candidate.id} already reported to TNS, skipping")
        return

    try:
        # Step 1: Prepare report data
        report_data = tns_report_details(candidate, first_name, last_name, at_type=at_type, comment=comment)

        # Step 2: Submit the report
        report_id = _submit_tns_report(candidate, report_data)

        # Step 3: Wait for and retrieve reply
        feedback_result = _wait_for_tns_reply(report_id)

        # Step 4: Process feedback and update candidate
        _process_tns_feedback(candidate, report_id, feedback_result, first_name, last_name)

    except requests.RequestException as e:
        logger.error(f"Network error during TNS reporting for candidate {candidate.id}: {e}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error during TNS reporting for candidate {candidate.id}: {e}")
        raise
    except KeyError as e:
        logger.error(f"Missing expected key in TNS response for candidate {candidate.id}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during TNS reporting for candidate {candidate.id}: {e}")
        raise


def set_reported_by_LAST(candidate_id: str) -> None:
    """
    Mark a candidate as reported by LAST.

    Updates the candidate's reported_by_LAST flag to True. This is used
    when a candidate has been manually confirmed as reported to TNS
    through external means.

    Args:
        candidate_id (str): The ID of the candidate to mark as reported

    Returns:
        None: Updates candidate record in database
    """
    # Import here to avoid circular import
    from django.shortcuts import get_object_or_404
    from .models import Candidate

    candidate = get_object_or_404(Candidate, id=candidate_id)
    candidate.reported_by_LAST = True
    try:
        if candidate.tns_name:
            candidate.save(check_tns=False)  # Avoid TNS check if not needed
        else:
            candidate.save()
        logger.info(f"Candidate {candidate_id} marked as reported by LAST.")
    except Exception as e:
        logger.error(f"Error saving candidate: {e}")
