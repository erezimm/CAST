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
    api_token = lasair_settings.get('API_TOKEN')
    if not api_token:
        print("LASAIR API credentials not set.")
        return None
    L = lasair_client(lasair_settings['api_token'], endpoint=LASAIR_ENDPOINT)

    result = L.cone(ra=candidate.ra, dec=candidate.dec,
                    radius=LASAIR_CONE_RADIUS, requestType='nearest')
    if 'object' in result:
        print(f"Found {result['object']} at separation {result['separation']:.2f} with radius {radius_arcsec}")
    else:
        print('No object found at radius %f' % radius_arcsec)
        return None
    
    object_id = result['object']
    lcs = L.lightcurves([object_id])
    dfresult = pd.DataFrame(lcs[0]['candidates']) 
    dfresult = dfresult[dfresult['jd'] > Time.now().jd - days_ago]

    SNT = 5.

    # TODO: from here down
    for obs in dfresult.iloc:
        obs_date = Time(obs.MJD,format='mjd').to_datetime()
        if obs.uJy/obs.duJy >= SNT:
            magnitude = obs.m  # Detection
            magnitude_error = obs.dm
            limit = None
        else:
            magnitude = None  # Non detection
            limit = obs.mag5sig
            magnitude_error = None
        
        if not photometry_exists(candidate, obs_date, magnitude, magnitude_error):
            CandidatePhotometry.objects.create(
                candidate=candidate,
                obs_date=make_aware(obs_date),
                magnitude=magnitude,  # Null if non-detection
                magnitude_error=magnitude_error,
                filter_band=obs.F,  # Use a mapping if needed to human-readable filter names
                telescope="ATLAS",
                instrument=f"ATLAS_{obs.F}",
                limit=limit
            )


    _red_det_mask = (df.fid==2) & ~df.candid.isna()
    _red_nondet_mask = (df.fid==2) & df.candid.isna()
    _green_det_mask = (df.fid==1) & ~df.candid.isna()
    _green_nondet_mask = (df.fid==1) & df.candid.isna()

    # Plot the detections
    plt.errorbar(df['phase'].values[_red_det_mask], 
                df['magpsf'].values[_red_det_mask],
                yerr = df['sigmapsf'].values[_red_det_mask],
                color = 'red', 
                marker='*',
                ls = '--'
                )

    plt.errorbar(df['phase'].values[_green_det_mask], 
                df['magpsf'].values[_green_det_mask],
                yerr = df['sigmapsf'].values[_green_det_mask],
                color = 'green', 
                marker='*',
                ls = '--'
                )

    # Plot the non detections
    plt.scatter(df['phase'].values[_red_nondet_mask], 
                df['diffmaglim'].values[_red_nondet_mask],
                color = 'red', 
                marker='v'
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