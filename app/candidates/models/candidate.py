from astropy.coordinates import SkyCoord
from astropy import units as u
from django.conf import settings
from django.urls import reverse

from candidates.tns_utils import tns_cone_search, check_if_reported_by_last
from django.db import models


CLASSIFICATION_CHOICES = [
    ('stellar', 'Stellar'),
    ('solar', 'Solar'),
    ('agn', 'AGN'),
]


class Candidate(models.Model):
    name = models.CharField(max_length=150)
    ra = models.FloatField()  # Right Ascension
    dec = models.FloatField()  # Declination
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
    reported_to_astro_colibri = models.BooleanField(default=False)
    host_galaxy = models.CharField(max_length=100, null=True, blank=True)  # Host galaxy name (if associated)
    dist_Mpc = models.FloatField(null=True)  # Distance to the host galaxy in Mpc
    redshift = models.FloatField(null=True)  # Redshift of the host galaxy
    ToO_name = models.CharField(max_length=100, null=True, blank=True)  # If ToO, the name of the target
    classification = models.CharField(
        max_length=10,
        choices=CLASSIFICATION_CHOICES,
        default=None,
        null=True,
        blank=True
    )
    marked_for_followup = models.BooleanField(default=False)  # Marked for follow-up observations

    def save(self, check_tns=False, *args, **kwargs):
        """
        Override the save method to generate the SDSS-style name using astropy.

        ``check_tns`` defaults to False so routine saves (real/bogus, follow-up, etc.)
        never trigger blocking TNS network calls. Pass ``check_tns=True`` only on the
        initial ingest, where resolving the TNS name is intended.
        """
        self.name = self.generate_LAST_name()
        if check_tns and settings.TNS_API_LOOKUPS_ENABLED:
            self.reported_by_LAST, self.tns_name = self.query_tns()
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

        Performs a cone search around the candidate's position and checks
        if any nearby objects were reported by LAST telescope.

        Returns:
            tuple: (reported_by_last: bool, tns_name: str or None)
                - reported_by_last: True if object was reported by LAST
                - tns_name: TNS object name if found, None otherwise
        """
        try:
            # Perform cone search to find nearby objects
            cone_result = tns_cone_search(self.ra, self.dec)

            if not cone_result or 'data' not in cone_result:
                return False, None

            cone_reply = cone_result['data']
            if not cone_reply:
                return False, None

            # Check each nearby object to see if it was reported by LAST
            for obj in cone_reply:
                objname = obj['objname']
                reported_by_last, name = check_if_reported_by_last(objname)

                if reported_by_last:
                    return True, name

                # If object exists but wasn't reported by LAST, return it anyway
                if name:
                    return False, name

            return False, None

        except Exception as e:
            # Log error but don't crash - just return not found
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error querying TNS for candidate {self.id}: {e}")
            return False, None

    def get_real_bogus_display(self):
        """
        Return a human-readable string for the real/bogus classification.
        """
        if self.real_bogus is True:
            return "Real"
        if self.real_bogus is False:
            return "Bogus"
        return "Neither"

    def __str__(self):
        return self.name
