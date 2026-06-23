import glob
import logging
import os
import traceback
from collections import Counter
from datetime import datetime, timedelta
from enum import Enum

from django.conf import settings

from candidates.models import CandidatePhotometry, CandidateDataProduct
from candidates.photometry_utils import add_photometry_from_last_report, get_atlas_fp, get_ztf_fp, get_lasair_api_token
from candidates.services.enrichment import add_ToO_names_to_candidate, update_candidate_cutouts, try_add_cutout, \
    try_forced_photometry, try_associate_host_galaxy
from candidates.services.identity import handle_candidate_identity
from candidates.services.parsing import parse_json_file, ensure_aware_utc
from candidates.utils import fetch_ps1_cutout, fetch_sdss_cutout

logger = logging.getLogger(__name__)
CAST_SETTINGS = settings.CAST_CANDIDATES

class IngestionResult(Enum):
    CREATED = "created"
    NO_JSON_FILES = "no_json_files"
    PARSE_FAILED = "parse_failed"
    INVALID_COORDS = "invalid_coords"
    CANDIDATE_EXISTS = "candidate_exists"
    UNKNOWN_ERROR = "unknown_error"

def process_json_file(file, lasair_enabled: bool = None) -> tuple[int, IngestionResult, str]:
    """
    Processes the uploaded JSON file and adds candidates to the database.
    :param file: Ingested json file object
    :return: The number of candidates successfully added
    """
    try:
        payload = parse_json_file(file)
    except ValueError as e:
        logger.warning(str(e))
        return 0, IngestionResult.PARSE_FAILED, None

    logger.info(f"Processing file: {file.name}")

    at_report = payload.at_report
    last_report = payload.last_report
    ra = payload.ra
    dec = payload.dec

    candidate, is_new = handle_candidate_identity(payload, file, lasair_enabled)

    if not is_new:
        logger.info(
            f"Candidate already exists for {file.name} "
            f"(RA={payload.ra}, Dec={payload.dec})"
        )
        return 0, IngestionResult.CANDIDATE_EXISTS, None

    instr_cfg = CAST_SETTINGS["instruments"]
    # ---- AT (old JSON format) photometry ----
    if not last_report:
        non_detection = at_report.get("non_detection", {})
        if non_detection:
            raw_obs_date = non_detection.get("obsdate", [None])[0]
            obs_date = ensure_aware_utc(raw_obs_date)
            limit = non_detection.get("flux")
            filter_value = non_detection.get("filter_value")

            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=obs_date,
                limit=limit,
                filter_band=str(filter_value),
                telescope=instr_cfg["last_telescope"],
                instrument=instr_cfg["last_instrument"],
            )

        photometry_data = at_report.get("photometry", {}).get("photometry_group", {})
        obs_date = photometry_data.get("obsdate", [None])[0]
        if obs_date:
            raw_obs_date = non_detection.get("obsdate", [None])[0]
            obs_date = ensure_aware_utc(raw_obs_date)
            magnitude = photometry_data.get("flux")
            filter_value = photometry_data.get("filter_value")

            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=obs_date,
                magnitude=magnitude,
                magnitude_error=instr_cfg["default_magnitude_error"],
                filter_band=str(filter_value),
                telescope=instr_cfg["last_telescope"],
                instrument=instr_cfg["last_instrument"],
            )

    if last_report:
        try:
            add_photometry_from_last_report(candidate, last_report)
        except Exception as e:
            logger.warning(f"Error adding photometry: {e}")

        add_ToO_names_to_candidate(candidate, last_report)

        update_candidate_cutouts(candidate)

    # ---- PS1 cutout ----
    try_add_cutout(
        candidate=candidate,
        fetch_func=fetch_ps1_cutout,
        ra=ra,
        dec=dec,
        product_type="ps1",
        name_suffix="PS1 cutout",
    )

    # ---- SDSS cutout ----
    try_add_cutout(
        candidate=candidate,
        fetch_func=fetch_sdss_cutout,
        ra=ra,
        dec=dec,
        product_type="sdss",
        name_suffix="SDSS cutout",
    )

    # ---- Forced photometry ----
    try_forced_photometry(candidate, get_atlas_fp, "Atlas")

    # Use cached credential check if provided, otherwise check directly
    if lasair_enabled is not None:
        should_do_ztf = lasair_enabled
    else:
        should_do_ztf = has_lasair_credentials()
    
    if should_do_ztf:
        try_forced_photometry(candidate, get_ztf_fp, "ZTF")
    else:
        logger.info(
            f"Skipping ZTF forced photometry for candidate {candidate.id}: "
            "LASAIR credentials not configured"
        )

    # ---- Host galaxy association ----
    try_associate_host_galaxy(candidate)

    return 1, IngestionResult.CREATED, candidate.name


# Step 1: Get JSON names already in DB
# Could be a problem if too large...
def get_json_names_from_db():
    return set(
        CandidateDataProduct.objects.filter(data_product_type='json')
        .values_list('name', flat=True)
    )

def set_time_cutoff(cutoff):
    return datetime.now() - timedelta(days=cutoff)


def collect_new_json(directory_path, cutoff_time, existing_json_names):
    """
    Collect JSON files newer than cutoff_time and not already ingested.
    Logs exclusion reasons for debugging.
    """
    if not os.path.isdir(directory_path):
        logger.warning(f"Directory does not exist: {directory_path}")
        return []

    all_files = glob.glob(os.path.join(directory_path, "*.json"))
    if not all_files:
        logger.info(f"No .json files found in {directory_path}")
        return []

    selected_files = []

    for file in all_files:
        basename = os.path.basename(file)
        mtime = datetime.fromtimestamp(os.path.getmtime(file))

        if basename in existing_json_names:
            logger.debug(f"Skipping {basename}: already ingested")
            continue

        if mtime <= cutoff_time:
            logger.debug(
                f"Skipping {basename}: too old "
                f"(mtime={mtime}, cutoff={cutoff_time})"
            )
            continue

        selected_files.append(file)

    logger.info(
        f"Selected {len(selected_files)} of {len(all_files)} JSON files"
    )
    return selected_files


def process_multiple_json_files(directory_path, cutoff=3, check_tns: bool = True) -> int:
    """
    Processes new JSON files from the last day that are not already in the database.
    :param directory_path: Path to the directory containing JSON files
    :return: The total number of candidates successfully added
    """

    # Step 1: Get JSON names already in DB
    existing_json_names = get_json_names_from_db()

    # Step 2: Set time cutoff to 24 hours ago
    cutoff_time = set_time_cutoff(cutoff)

    # Step 3: Collect new JSON files
    json_files = collect_new_json(directory_path, cutoff_time, existing_json_names)

    if not json_files:
        logger.info(
            f"No new JSON files found in {directory_path} "
            f"(cutoff={cutoff} days)"
        )
        return 0

    logger.info(f"Found {len(json_files)} new files to process.")
    total_candidates_added = 0

    # Check credentials once at the beginning
    lasair_enabled = has_lasair_credentials()
    logger.info(
        "Enrichment capabilities: "
        f"LASAIR={'enabled' if lasair_enabled else 'disabled'}"
    )

    summary = Counter()
    # Step 4: Process each new file
    for json_file in json_files:
        try:
            with open(json_file, 'rb') as file:
                count, result, candidate_name = process_json_file(file, lasair_enabled)

                if result == IngestionResult.CREATED:
                    total_candidates_added += count
                    # Use the candidate's generated name for success messages
                    if candidate_name:
                        logger.info(f"{candidate_name}: new candidate created")
                    else:
                        logger.info(f"{os.path.basename(json_file)}: new candidate created")

                elif result == IngestionResult.CANDIDATE_EXISTS:
                    logger.info(f"{file.name}: candidate already exists")

                elif result == IngestionResult.PARSE_FAILED:
                    logger.warning(f"{file.name}: parse failed")

                elif result == IngestionResult.INVALID_COORDS:
                    logger.warning(f"{file.name}: invalid coordinates")

                else:
                    logger.warning(f"{file.name}: ingestion skipped ({result.value})")

                summary[result] += 1

        except Exception as e:
            logger.error(f"Error processing {os.path.basename(json_file)}: {e}")
            traceback.print_exc()

    logger.info("Ingestion summary:")
    for result, n in summary.items():
        logger.info(f"  {result.value}: {n}")

    logger.info(f"Total candidates added: {total_candidates_added}")
    return total_candidates_added


def has_lasair_credentials() -> bool:
    return bool(get_lasair_api_token())
