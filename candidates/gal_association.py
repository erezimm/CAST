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
        np.logical_and(glade['RA'] > ra-0.5, glade['RA'] < ra+0.5),
        np.logical_and(glade['Dec'] > dec-0.5, glade['Dec'] < dec+0.5)
        )]
    glade_coo = SkyCoord(mini_glade['RA'], mini_glade['Dec'],frame='icrs',unit='deg')

    target_coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    idx, sep2d, _ = target_coord.match_to_catalog_sky(glade_coo)

    if sep2d.arcsecond <= radius:
        gal = mini_glade.iloc[idx]
        gal_name = next(
            (f"{gal[col]} ({col})" for col in ["GWGC", "HyperLEDA", "2MASS", "wiseX", "SDSS-DR16Q"] if not pd.isna(gal[col])),
            None)  # Get the first non-null galaxy name by priority of catalogs
        logger.info(f"Found galaxy: {gal_name} with separation {sep2d.arcsecond[0]:.2f} arcseconds and distance {gal.d_L:.2f} Mpc.")
        return gal_name, gal.d_L, gal.z_helio
    else:
        logger.error(f"No galaxy found within {radius} arcseconds for candidate at RA: {ra}, Dec: {dec}.")
        return None, None, None