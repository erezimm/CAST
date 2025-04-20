from django.db import models
from astropy.coordinates import SkyCoord
from astropy import units as u
from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver
import requests
import json
import os

def tns_cone_search(ra, dec, radius=3.0):
    """
    Perform a cone search on the Transient Name Server.

    Parameters:
        ra (float): Right Ascension in degrees.
        dec (float): Declination in degrees.
        radius (float): Search radius in arcsec.

    Returns:
        dict: The response data from the TNS.
    """
    # API endpoint for the cone searchtns_settings = settings.BROKERS.get('TNS', {})
    test = settings.TNS_TEST
    if test:
        TNS                 = "sandbox.wis-tns.org"
    else:
        TNS                 = "www.wis-tns.org"
    url_tns_api         = "https://" + TNS + "/api"
    tns_settings = settings.BROKERS.get('TNS', {})
    TNS_BOT_ID          = tns_settings.get('bot_id')
    TNS_BOT_NAME        = tns_settings.get('bot_name')
    TNS_API_KEY         = tns_settings.get('api_key')

    endpoint = f"{url_tns_api}/get/search"
    
    # Headers required for the TNS API
    tns_marker = 'tns_marker{"tns_id": "' + str(TNS_BOT_ID) + '", "type": "bot", "name": "' + TNS_BOT_NAME + '"}'
    headers = {'User-Agent': tns_marker}

    # Parameters for the cone search
    payload = {
        "api_key": TNS_API_KEY,
        "data": json.dumps({
            "ra": str(ra),
            "dec": str(dec),
            "radius": str(radius),
            "units": "arcsec"
        })
    }
    # Perform the request
    try:
        response = requests.post(endpoint, headers=headers, data=payload, timeout=10)
        response.raise_for_status()  # Raise an error for bad status codes
        return response.json()  # Parse the JSON response
    except requests.RequestException as e:
        print(f"Error during TNS cone search: {e}")
        return None
    
class Candidate(models.Model):
    name = models.CharField(max_length=100)
    ra = models.FloatField()   # Right Ascension
    dec = models.FloatField()   # Declination
    file_source = models.FileField(upload_to='candidate_files/')  # Optional: To track file origin
    discovery_datetime = models.DateTimeField(null=True, blank=True)
    real_bogus = models.BooleanField(
        null=True,  # Allows for a "neither" state
        blank=True,
        default=None  # Default is "neither"
    )
    real_bogus_user = models.CharField(max_length=100, null=True, blank=True)  # User who classified the candidate
    created_at = models.DateTimeField(auto_now_add=True)
    tns_name = models.CharField(max_length=100, null=True, blank=True)  # TNS name (if reported)
    reported_by_LAST = models.BooleanField(default=False)  # Reported by LAST
    host_galaxy = models.CharField(max_length=100, null=True, blank=True)  # Host galaxy name (if associated)
    dist_Mpc = models.FloatField(null=True)  # Distance to the host galaxy in Mpc
    redshift = models.FloatField(null=True)  # Redshift of the host galaxy
    
    def save(self, check_tns=True, *args, **kwargs):
        """
        Override the save method to generate the SDSS-style name using astropy.
        """
        self.name = self.generate_LAST_name()
        if check_tns:
            self.reported_by_LAST,self.tns_name = self.query_tns()
        super().save(*args, **kwargs)

    def generate_LAST_name(self):
        """
        Generate an SDSS-style name using astropy's SkyCoord for RA and Dec formatting.
        """
        # Create a SkyCoord object for the RA and Dec
        coord = SkyCoord(ra=self.ra * u.degree, dec=self.dec * u.degree)

        # Convert to SDSS-style format
        ra_str = coord.ra.to_string(unit=u.hour, sep='', precision=2, pad=True)  # hh:mm:ss.dd
        dec_str = coord.dec.to_string(unit=u.degree, sep='', precision=2, alwayssign=True, pad=True)  # ±dd:mm:ss.dd

        return f"LAST J{ra_str}{dec_str}"
    
    def query_tns(self):
        """
        Query the TNS to check if a candidate already exists.
        :param candidate: Candidate instance
        :return: TNS response (JSON) or None if not found
        """
        tns_settings = settings.BROKERS.get('TNS', {})
        test = settings.TNS_TEST
        if test:
            TNS                 = "sandbox.wis-tns.org"
        else:
            TNS                 = "www.wis-tns.org"
        url_tns_api         = "https://" + TNS + "/api"
        TNS_BOT_ID          = tns_settings.get('bot_id')
        TNS_BOT_NAME        = tns_settings.get('bot_name')
        TNS_API_KEY         = tns_settings.get('api_key')
        
        # API endpoint for the cone search
        endpoint = f"{url_tns_api}/get/object"
            
        # Headers required for the TNS API
        tns_marker = 'tns_marker{"tns_id": "' + str(TNS_BOT_ID) + '", "type": "bot", "name": "' + TNS_BOT_NAME + '"}'
        headers = {'User-Agent': tns_marker}
        try:
            # Perform a cone search to get the object name
            cone = tns_cone_search(self.ra, self.dec)
            # logger.debug(f'cone search result: {cone}')
            if 'data' not in cone.keys():
                return False, None
            cone_reply = cone['data']
            if cone_reply != []:
                # logger.debug(f'cone search result: {cone_reply}')
                for obj in cone_reply:
                    objname = obj['objname']
                    # Parameters for the cone search
                    payload = {
                        "api_key": TNS_API_KEY,
                        "data": json.dumps({
                            "objname" : str(objname),
                            "photometry" : "1",
                            "spectra" : "0"
                        })
                    }
                    # Perform the request
                    response = requests.post(endpoint, headers=headers, data=payload)
                    response.raise_for_status()  # Raise an error for bad status codes
                    result = response.json()
                    reply = result['data']
                    phot = reply['photometry']
                    for p in phot:
                        print(p['instrument']['name'])
                        # logger.debug(f'photometry: {p}')
                        if p['instrument']['name'] == 'LAST-Cam':
                            return True, objname
                if len(obj) > 0:
                    try:
                        return False, objname
                    except Exception as e:
                        return False, None
                # logger.debug(f'Object was not reported by LAST')
                return False , None
                        
            else:
                return False, None
        except requests.RequestException as e:
            return False, None
        except Exception as e:
            return False, None


    def get_real_bogus_display(self):
        """
        Return a human-readable string for the real/bogus classification.
        """
        if self.real_bogus is True:
            return "Real"
        elif self.real_bogus is False:
            return "Bogus"
        return "Neither"
    
    def __str__(self):
        return self.name
    
class CandidatePhotometry(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="photometry")
    obs_date = models.DateTimeField()  # Observation date
    magnitude = models.FloatField(null=True, blank=True)  # Magnitude measurement
    magnitude_error = models.FloatField(null=True, blank=True)  # Error on magnitude
    filter_band = models.CharField(max_length=20, null=True, blank=True)  # e.g., 'g', 'r', 'i'
    telescope = models.CharField(max_length=100, null=True, blank=True)  # Telescope name
    instrument = models.CharField(max_length=100, null=True, blank=True)  # Instrument name
    limit = models.FloatField(null=True, blank=True)  # Magnitude limit (if no detection)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.candidate.name} - {self.obs_date} - {self.filter_band}"


def candidate_data_product_path(instance, filename):
    return f'candidates/{instance.candidate.name}/{filename}'
            

class CandidateDataProduct(models.Model):
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name='data_products',
        null=False
    )
    datafile = models.FileField(upload_to=candidate_data_product_path, blank=True, null=True)
    data_product_type = models.CharField(max_length=50, choices=[('ref', 'ref'), ('new', 'new'), ('diff','diff'), ('ps1','ps1'),('sdss','sdss'),('json','json'),('tns_report','tns_report')])
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def delete(self, *args, **kwargs):
        """
        Override the delete method to ensure the associated file is deleted.
        """
        if self.datafile and os.path.isfile(self.datafile.path):
            try:
                os.remove(self.datafile.path)
            except Exception as e:
                print(f"Error deleting file {self.datafile.path}: {e}")
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (Candidate: {self.candidate.name})"

# Signal to delete associated files when a CandidateDataProduct is deleted
@receiver(post_delete, sender=CandidateDataProduct)
def delete_datafile(sender, instance, **kwargs):
    """
    Deletes the file associated with a CandidateDataProduct when the instance is deleted.
    """
    if instance.datafile and os.path.isfile(instance.datafile.path):
        try:
            os.remove(instance.datafile.path)
        except Exception as e:
            print(f"Error deleting file {instance.datafile.path}: {e}")

# Signal to delete associated data products when a Candidate is deleted
@receiver(post_delete, sender=Candidate)
def delete_candidate_data_products(sender, instance, **kwargs):
    """
    Deletes all data products associated with a Candidate when the Candidate is deleted.
    """
    data_products = CandidateDataProduct.objects.filter(candidate=instance)
    for data_product in data_products:
        data_product.delete()

class CandidateAlert(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="alert")
    filename = models.CharField(max_length=100, null=True, blank=True)
    discovery_datetime = models.DateTimeField(null=True, blank=True)
    fieldid = models.IntegerField(null=True, blank=True)
    subimage = models.IntegerField(null=True, blank=True)
    mount = models.IntegerField(null=True, blank=True)
    camera = models.IntegerField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    reference = models.CharField(max_length=100, null=True, blank=True)
    reference_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ref_cutout_filename = models.CharField(max_length=255, null=True, blank=True)
    new_cutout_filename = models.CharField(max_length=255, null=True, blank=True)
    diff_cutout_filename = models.CharField(max_length=255, null=True, blank=True)
    
    def __str__(self):
        return self.candidate.name