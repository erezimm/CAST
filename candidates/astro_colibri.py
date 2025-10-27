import json
import datetime
import requests

# Django imports
from django.conf import settings

# Logging
import logging
logger = logging.getLogger(__name__)


colibri_settings = settings.ASTRO_COLIBRI
api_url = colibri_settings['api_url']
auth_filename = colibri_settings['auth_file']
with open(auth_filename, "r") as f:
    auth_pass = f.read().strip()
auth = requests.auth.HTTPBasicAuth("last", auth_pass)


def update_firestore_db(data):
    request = requests.post(api_url + "/add_last_transient", json=data, auth=auth)
    request.raise_for_status()
    logger.info("Sent to Astro-COLIBRI successfully. Response code: {}".format(request.status_code))
    logger.info(request.json())


def send_to_astri_colibri(candidate):

    detections = candidate.photometry.filter(magnitude__isnull=False).order_by('obs_date')
    first_detection = detections.first()
    last_detection = detections.last()
    peak_detection = detections.order_by('magnitude').first()

    data = {
            "timestamp": candidate.discovery_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_id": "last_stellar_" + candidate.discovery_datetime.strftime("%Y%m%d%H%M%S"),
            "source_name": candidate.generate_LAST_name(),  # name that will appear as source name in Astro-COLIBRI
            "ra": candidate.ra,
            "dec": candidate.dec,
            "type": "star",  # "star" for stellar flares or other optical transients associated to stars; "ot_sn" for SNe; "ot_other" for classified optical transients other than SNe or stellar events
            "observatory": "last",
            
            # Optional fields
            "transient_flux": first_detection.magnitude,
            "transient_flux_units": "mag",
            "err": 1.25/3600,  # uncertainty radius of event localisation in deg
            "photometry": {
                "last_clear": {  # Filter name format: observatory_band (lowercase)
                    "first": {
                        "time": first_detection.obs_date.strftime("%Y-%m-%d %H:%M:%S"),
                        "filter": "last_clear",
                        "mag": first_detection.magnitude,
                        "mag_err": first_detection.magnitude_error,
                    },
                    "peak": {
                        "time": peak_detection.obs_date.strftime("%Y-%m-%d %H:%M:%S"),
                        "filter": "last_clear",
                        "mag": peak_detection.magnitude,
                        "mag_err": peak_detection.magnitude_error,
                    },
                    "last": {
                        "time": last_detection.obs_date.strftime("%Y-%m-%d %H:%M:%S"),
                        "filter": "last_clear",
                        "mag": last_detection.magnitude,
                        "mag_err": last_detection.magnitude_error,
                    },
                },
            },
        }

    logger.info("Sending candidate {} to Astro-COLIBRI".format(candidate.id))
    logger.info(json.dumps(data, indent=4))
    update_firestore_db(data)