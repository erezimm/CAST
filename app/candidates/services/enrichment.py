import logging

from candidates.gal_association import associate_galaxy
from candidates.models import CandidateDataProduct, CandidateAlert
from candidates.utils import create_candidate_cutouts

logger = logging.getLogger(__name__)

def try_add_cutout(
    *,
    candidate,
    fetch_func,
    ra,
    dec,
    product_type: str,
    name_suffix: str,
):
    """
    Best-effort helper to fetch an external cutout and save it
    as a CandidateDataProduct.
    """
    try:
        cutout = fetch_func(ra, dec)
    except Exception as e:
        logger.warning(
            f"Error fetching {product_type} cutout for candidate {candidate.id}: {e}"
        )
        return

    if not cutout:
        return

    CandidateDataProduct.objects.create(
        candidate=candidate,
        datafile=cutout,
        data_product_type=product_type,
        name=f"{candidate.name} {name_suffix}",
    )

def try_forced_photometry(candidate, fetch_func, label: str):
    """
    Best-effort helper for forced photometry ingestion.
    """
    try:
        fetch_func(candidate)
    except Exception as e:
        logger.warning(
            f"Error fetching {label} photometry for candidate {candidate.id}: {e}"
        )

def try_associate_host_galaxy(candidate):
    """
    Best-effort host galaxy association.
    """
    if candidate.host_galaxy:
        return

    try:
        gal_name, dist_Mpc, z = associate_galaxy(
            candidate.ra,
            candidate.dec,
        )
    except Exception as e:
        logger.warning(
            f"Error associating galaxy for candidate {candidate.id}: {e}"
        )
        return

    if not gal_name:
        return

    candidate.host_galaxy = gal_name
    candidate.dist_Mpc = dist_Mpc
    candidate.redshift = z
    candidate.save()

def update_candidate_cutouts(candidate):
    last_alert = CandidateAlert.objects.filter(candidate=candidate).order_by('-created_at').first()
    if not last_alert:
        return None


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
    except Exception as e:
        logger.error(f"Error creating cutouts for candidate {candidate.id}: {e}")
        return None

def add_ToO_names_to_candidate(candidate, last_report):
    """
    Check if candidate is ToO, and if so - add the ToO name to the candidate.
    :param candidate: Candidate instance
    :param last_report: last report section from the json file
    :return: None
    """
    if not last_report:
        return

    object_name = last_report.get('object')
    field = last_report.get('field')
    if not object_name or object_name == field:
        return

    try:
        parts = object_name.split(".")
        if len(parts) < 2:
            return

        ToO_name = parts[1]
        logger.info(f"Found ToO name: {ToO_name} for candidate {candidate.id}")
        candidate.ToO_name = ToO_name
        candidate.save(check_tns=False)

    except Exception as e:
        logger.error(
            f"Error saving ToO name for candidate {candidate.id}: {e}"
        )
