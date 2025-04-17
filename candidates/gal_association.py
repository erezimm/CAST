# Built-in imports
import os

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

GLADE_CAT_PATH = os.path.join(settings.BASE_DIR, "data", "catalogs", "GLADE_for_CAST.csv")
glade = None  # Global variable to store the GLADE catalog, instead of initializing it every time


def get_glade():
    """
    Load the GLADE catalog file if not already loaded.
    Avoids long initialization time of the server, and loads the catalog only when first needed.
    """
    global glade
    if glade is None:
        logger.info("Loading GLADE catalog...")
        glade = pd.read_csv(GLADE_CAT_PATH, dtype=dtype_mapping)
    return glade


def get_glade_coo(glade, ra, dec):
    """
    Get the SkyCoord object of the GLADE catalog within a 1 deg^2 of the given RA and Dec.
    Used for quicker initialization of the SkyCoord object.

    Returns:
        glade_coo: SkyCoord object containing the coordinates of the GLADE catalog within the specified area.
    """
    logger.info("Loading GLADE catalog coordinates...")
    mini_glade = glade[np.logical_and(
        np.logical_and(glade['RA'] > ra-0.5, glade['RA'] < ra+0.5),
        np.logical_and(glade['Dec'] > dec-0.5, glade['Dec'] < dec+0.5)
        )]
    glade_coo = SkyCoord(mini_glade['RA'], mini_glade['Dec'],frame='icrs',unit='deg')
    return glade_coo


def associate_galaxy(ra, dec, radius=30.0):
    """
    Find the galaxy in the catalog that is most likely associated with the given RA, Dec.

    Parameters:
        ra (float): Right Ascension of the target in degrees.
        dec (float): Declination of the target in degrees.
        radius (float): Search radius in arcseconds.

    Returns:
        tuple (galaxy name, d_L in Mpc). (None, None) if no galaxy is found within the radius.
    """
    glade = get_glade()
    glade_coo = get_glade_coo(glade, ra, dec)

    target_coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    idx, sep2d, _ = target_coord.match_to_catalog_sky(glade_coo)

    if sep2d.arcsecond <= radius:
        gal = glade.iloc[idx]
        if not pd.isna(gal.wiseX):
            logger.info(f"Found galaxy: {gal.wiseX} with separation {sep2d.arcsecond[0]:.2f} arcseconds.")
            return f"{gal.wiseX} (wiseX)", gal.d_L
        else:
            logger.info(f"Found galaxy: {gal['SDSS-DR16Q']} with separation {sep2d.arcsecond[0]:.2f} arcseconds.")
            return f"{gal['SDSS-DR16Q']} (SDSS-DR16Q)", gal.d_L
    else:
        logger.error(f"No galaxy found within {radius} arcseconds for candidate at RA: {ra}, Dec: {dec}.")
        return None, None