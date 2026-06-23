import os

from django.core.files import File
from candidates.gal_association import associate_galaxy
from candidates.models import CandidateAlert, CandidateDataProduct, Candidate
from candidates.photometry_utils import get_atlas_fp, get_ztf_fp, add_photometry_from_last_report
from candidates.services.enrichment import add_ToO_names_to_candidate, update_candidate_cutouts
from candidates.utils import CAST_SETTINGS, \
    cone_search_filter_candidates


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
    exists = matching_candidates.exists()
    return exists, matching_candidates.first() if exists else None

def handle_candidate_identity(payload, file, lasair_enabled: bool = None):
    """
    Handle candidate existence check and creation.

    Args:
        payload: Parsed alert payload
        file: Ingested json file object
        lasair_enabled: Cached LASAIR credential status (optional)

    Returns:
        (candidate, is_new)
    """
    ra = payload.ra
    dec = payload.dec
    discovery_datetime = payload.discovery_datetime
    last_report = payload.last_report

    candidate_exists, existing_candidate = check_candidate_exists_by_cone(
        ra,
        dec,
        radius_arcsec=CAST_SETTINGS["cone_search_radius_arcsec"],
    )

    if candidate_exists:
        # ---- Existing candidate path ----
        save_alert(existing_candidate, discovery_datetime, file.name, last_report)

        if last_report:
            wrapped_file = File(file)
            wrapped_file.name = payload.filename

            CandidateDataProduct.objects.create(
                candidate=existing_candidate,
                datafile=wrapped_file,
                data_product_type="json",
                name=wrapped_file.name,
            )

            add_ToO_names_to_candidate(existing_candidate, last_report)
            add_photometry_from_last_report(existing_candidate, last_report)

            # update candidate cutouts
            update_candidate_cutouts(existing_candidate)

            # forced photometry - only if credentials are available
            get_atlas_fp(existing_candidate)
            
            # Check credentials before ZTF photometry
            if lasair_enabled is not None:
                should_do_ztf = lasair_enabled
            else:
                from django.conf import settings
                should_do_ztf = bool(settings.LASAIR_API_KEY)
            
            if should_do_ztf:
                get_ztf_fp(existing_candidate)

        if not existing_candidate.host_galaxy:
            gal_name, dist_Mpc, z = associate_galaxy(
                existing_candidate.ra,
                existing_candidate.dec,
            )
            if gal_name:
                existing_candidate.host_galaxy = gal_name
                existing_candidate.dist_Mpc = dist_Mpc
                existing_candidate.redshift = z
                existing_candidate.save()

        return existing_candidate, False

    # ---- New candidate path ----
    # Resolve the TNS name on initial ingest (honors settings.TNS_API_LOOKUPS_ENABLED);
    # routine saves elsewhere keep the safe check_tns=False default.
    candidate = Candidate(
        ra=ra,
        dec=dec,
        discovery_datetime=discovery_datetime,
    )
    candidate.save(check_tns=True)

    save_alert(candidate, discovery_datetime, file.name, last_report)

    wrapped_file = File(file)
    wrapped_file.name = payload.filename

    CandidateDataProduct.objects.create(
        candidate=candidate,
        datafile=wrapped_file,
        data_product_type="json",
        name=payload.filename,
    )

    return candidate, True

def save_alert(candidate, discovery_datetime, filename, last_report=None):
    """
    Save the candidate as an alert in the database.
    """
    alert_data = {
        "candidate": candidate,
        "filename": os.path.basename(filename),
        "discovery_datetime": discovery_datetime,
    }

    if last_report:
        alert_data.update(_extract_alert_data_from_last_report(last_report))
    else:
        alert_data.update(_extract_alert_data_from_filename(filename))

    return CandidateAlert.objects.create(**alert_data)

def _extract_alert_data_from_last_report(last_report: dict) -> dict:
    return {
        "mount": last_report.get("mount"),
        "camera": last_report.get("camera"),
        "fieldid": last_report.get("field"),
        "subimage": last_report.get("cropid"),
        "score": last_report.get("score"),
        "ref_cutout_filename": last_report.get("ref_cutout"),
        "new_cutout_filename": last_report.get("new_cutout"),
        "diff_cutout_filename": last_report.get("diff_cutout"),
    }

def _extract_alert_data_from_filename(filename: str) -> dict:
    """
    Expected filename format:
    <prefix>.<prefix>.<mount>.<camera>_..._<fieldid>_..._<subimage>_...
    """
    attributes = filename.split("_")

    try:
        prefix_parts = attributes[0].split(".")
        return {
            "mount": prefix_parts[2],
            "camera": prefix_parts[3],
            "fieldid": attributes[3],
            "subimage": attributes[6],
        }
    except (IndexError, AttributeError) as exc:
        raise ValueError(f"Invalid alert filename format: {filename}") from exc