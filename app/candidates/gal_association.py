# Built-in imports
import os
from pathlib import Path

# Third-party imports
import numpy as np
import pandas as pd
from astropy import units as u
from astropy.coordinates import SkyCoord

# Django imports
from django.conf import settings

# Logging
import logging

logger = logging.getLogger(__name__)

# Load the GLADE catalog to memory
dtype_mapping = {
    "GWGC": "str",
    "HyperLEDA": "str",
    "2MASS": "str",
    "wiseX": "str",
    "SDSS-DR16Q": "str",
}

GLADE_COLUMNS = [
    "GLADE_no",
    "PGC_no",
    "GWGC",
    "HyperLEDA",
    "2MASS",
    "wiseX",
    "SDSS-DR16Q",
    "object_type_flag",
    "RA",
    "Dec",
    "B",
    "B_err",
    "B_flag",
    "B_Abs",
    "J",
    "J_err",
    "H",
    "H_err",
    "K",
    "K_err",
    "W1",
    "W1_err",
    "W2",
    "W2_err",
    "W1_flag",
    "B_J",
    "B_J_err",
    "z_helio",
    "z_cmb",
    "z_flag",
    "v_err",
    "z_err",
    "d_L",
    "d_L_err",
    "dist_flag",
    "Mstar",
    "Mstar_err",
    "Mstar_flag",
    "merger_rate",
    "merger_rate_err",
]

GLADE_REQUIRED_COLUMNS = [
    "GWGC",
    "HyperLEDA",
    "2MASS",
    "wiseX",
    "SDSS-DR16Q",
    "RA",
    "Dec",
    "d_L",
    "dist_flag",
    "z_helio",
]

GLADE_CAT_PATH = os.path.join(settings.BASE_DIR, "large_files", "catalogs", "GLADE_for_CAST.csv")
glade = None  # Global variable to store the GLADE catalog, instead of initializing it every time


def _resolve_glade_path() -> Path:
    configured_path = Path(GLADE_CAT_PATH)
    if configured_path.exists():
        return configured_path

    txt_fallback = configured_path.with_suffix(".txt")
    if txt_fallback.exists():
        return txt_fallback

    return configured_path


def _looks_like_csv(path: Path) -> bool:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            return "," in stripped
    return False


def _load_glade_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        usecols=GLADE_REQUIRED_COLUMNS,
        dtype=dtype_mapping,
    )


def _load_glade_txt(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        sep=r"\s+",
        header=None,
        names=GLADE_COLUMNS,
        usecols=GLADE_REQUIRED_COLUMNS,
        dtype=dtype_mapping,
        na_values=["null"],
    )


def get_glade():
    """
    Load the GLADE catalog file if not already loaded.
    Avoids long initialization time of the server, and loads the catalog only when first needed.
    """
    global glade
    if glade is None:
        glade_path = _resolve_glade_path()
        logger.info(f"Loading GLADE catalog from {glade_path}...")
        glade = _load_glade_csv(glade_path) if _looks_like_csv(glade_path) else _load_glade_txt(glade_path)
    return glade


def associate_galaxy(ra, dec, radius=30.0):
    """
    Find the galaxy in the catalog that is most likely associated with the given RA, Dec.

    Parameters:
        ra (float): Right Ascension of the target in degrees.
        dec (float): Declination of the target in degrees.
        radius (float): Search radius in arcseconds.

    Returns:
        tuple (galaxy name, d_L in Mpc, redshift). (None, None, None) if no galaxy is found within the radius.
    """
    glade = get_glade()

    # Create a 1 deg^2 area around the target coordinates, for faster SkyCoord match
    mini_glade = glade[np.logical_and(
        np.logical_and(glade['RA'] > ra - 0.5, glade['RA'] < ra + 0.5),
        np.logical_and(glade['Dec'] > dec - 0.5, glade['Dec'] < dec + 0.5)
    )]
    if mini_glade.empty:
        logger.error(f"No galaxy found within {radius} arcseconds for candidate at RA: {ra}, Dec: {dec}.")
        return None, None, None

    glade_coo = SkyCoord(mini_glade['RA'], mini_glade['Dec'], frame='icrs', unit='deg')

    target_coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    idx, sep2d, _ = target_coord.match_to_catalog_sky(glade_coo)

    if sep2d.arcsecond <= radius:
        gal = mini_glade.iloc[idx]
        gal_name = next(
            (f"{gal[col]} ({col})" for col in ["GWGC", "HyperLEDA", "2MASS", "wiseX", "SDSS-DR16Q"] if
             not pd.isna(gal[col])),
            None)  # Get the first non-null galaxy name by priority of catalogs
        redshift = gal.z_helio if gal.dist_flag in {2, 3} else None
        logger.info(
            f"Found galaxy: {gal_name} with separation {sep2d.arcsecond[0]:.2f} arcseconds and distance {gal.d_L:.2f} Mpc.")
        return gal_name, gal.d_L, redshift
    else:
        logger.error(f"No galaxy found within {radius} arcseconds for candidate at RA: {ra}, Dec: {dec}.")
        return None, None, None
