# Built-in imports
import os

# Third-party imports
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

glade_cat_path = os.path.join(settings.BASE_DIR, "data", "catalogs", "GLADE_for_CAST.csv")
glade = None
glade_coo = None

def get_glade():
    """
    Load the GLADE catalog file if not already loaded.
    Avoids long initialization time of the server, and loads the catalog only when first needed.
    """
    global glade
    if glade is None:
        print("Loading GLADE catalog...")
        glade = pd.read_csv(glade_cat_path, dtype=dtype_mapping)
    return glade


def get_glade_coo():
    """
    Load the GLADE catalog coordinates if not already loaded.
    """
    global glade_coo
    if glade_coo is None:
        print("Loading GLADE catalog coordinates...")
        glade = get_glade()
        glade_coo = SkyCoord(glade['RA'], glade['Dec'],frame='icrs',unit='deg')
    return glade_coo


def associate_galaxy(ra, dec, radius=30.0):
    """
    Find the galaxy in the catalog that is most likely associated with the given RA, Dec.

    Parameters:
        ra (float): Right Ascension of the target in degrees.
        dec (float): Declination of the target in degrees.
        radius (float): Search radius in arcseconds.

    Returns:
        pandas.Series: The associated galaxy's data or None if no galaxy is within the radius.
    """
    glade = get_glade()
    glade_coo = get_glade_coo()

    target_coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame='icrs')
    idx, sep2d, _ = target_coord.match_to_catalog_sky(glade_coo)

    if sep2d.arcsecond <= radius:
        gal = glade.iloc[idx]
        if not pd.isna(gal.wiseX):
            return f"{gal.wiseX} (wiseX)"
        else:
            return f"{gal['SDSS-DR16Q']} (SDSS-DR16Q)"
    else:
        return None