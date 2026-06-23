from candidates.models.candidate import Candidate
from django.db import models


class CandidatePhotometry(models.Model):
    candidate = models.ForeignKey("candidates.Candidate", on_delete=models.CASCADE, related_name="photometry")
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
