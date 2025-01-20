from django.db import models
from astropy.coordinates import SkyCoord
from astropy import units as u

class Candidate(models.Model):
    name = models.CharField(max_length=100)
    ra = models.FloatField()   # Right Ascension
    dec = models.FloatField()   # Declination
    file_source = models.FileField(upload_to='candidate_files/')  # Optional: To track file origin
    discovery_datetime = models.DateTimeField(null=True, blank=True)
    # image = models.ImageField(upload_to='candidate_images/', null=True, blank=True)  # Image field
    real_bogus = models.BooleanField(
        null=True,  # Allows for a "neither" state
        blank=True,
        default=None  # Default is "neither"
    )
    created_at = models.DateTimeField(auto_now_add=True)


    
    def save(self, *args, **kwargs):
        """
        Override the save method to generate the SDSS-style name using astropy.
        """
        self.name = self.generate_LAST_name()
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