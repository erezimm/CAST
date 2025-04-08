import pandas as pd
from astropy.time import Time
from datetime import timedelta
from lasair import lasair_client

from django.conf import settings
from django.utils.timezone import make_aware

from .models import CandidatePhotometry

LASAIR_ENDPOINT = "https://lasair-ztf.lsst.ac.uk/api"
LASAIR_CONE_RADIUS = 5.0  # arcseconds

def get_ztf_fp(candidate, days_ago=10):
    """
    Get the ZTF forced photometry for a candidate.
    """

    lasair_settings = settings.BROKERS.get('LASAIR', {})
    api_token = lasair_settings.get('api_token')
    if not api_token:
        print("LASAIR API credentials not set.")
        raise ValueError("LASAIR API credentials not set.")
    L = lasair_client(lasair_settings['api_token'], endpoint=LASAIR_ENDPOINT)

    result = L.cone(ra=candidate.ra, dec=candidate.dec,
                    radius=LASAIR_CONE_RADIUS, requestType='nearest')
    if 'object' in result:
        print(f"Found {result['object']} at separation {result['separation']:.2f} with radius {LASAIR_CONE_RADIUS}")
    else:
        print('No object found at radius %f' % LASAIR_CONE_RADIUS)
        return None
    
    object_id = result['object']
    lcs = L.lightcurves([object_id])
    dfresult = pd.DataFrame(lcs[0]['candidates']) 
    dfresult = dfresult[dfresult['jd'] > Time.now().jd - days_ago]
    
    for obs in dfresult.iloc:
        obs_date = Time(obs.jd,format='jd').to_datetime()
        if obs.candid:
            magnitude = obs.magpsf  # Detection
            magnitude_error = obs.sigmapsf
            limit = None
        else:
            magnitude = None  # Non detection
            limit = obs.diffmaglim
            magnitude_error = None
        
        if not photometry_exists(candidate, obs_date, magnitude, magnitude_error):
            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=make_aware(obs_date),
                magnitude=magnitude,  # Null if non-detection
                magnitude_error=magnitude_error,
                filter_band='g' if obs.fid ==1 else 'r',  # fid=1 green, fid=2 red
                telescope="ZTF",
                limit=limit
            )

def photometry_exists(candidate, obs_date, magnitude, magnitude_error, filter_band='clear'):
    """
    Check if a photometry entry already exists for a given candidate, observation date, magnitude, and filter.
    """
    obs_date = make_aware(obs_date)
    query = CandidatePhotometry.objects.filter(
        candidate=candidate,
        obs_date__gte=obs_date - timedelta(seconds=5),  # Allowing 5s tolerance
        obs_date__lte=obs_date + timedelta(seconds=5),
        filter_band=filter_band
    )

    # Check for both magnitude and magnitude error if it’s a detection
    if magnitude is not None:
        query = query.filter(magnitude=magnitude, magnitude_error=magnitude_error)
    
    return query.exists()