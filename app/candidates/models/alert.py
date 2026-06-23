from candidates.models import Candidate
from django.db import models



class CandidateAlert(models.Model):
    candidate = models.ForeignKey("candidates.Candidate", on_delete=models.CASCADE, related_name="alert")
    filename = models.CharField(max_length=150, null=True, blank=True)
    discovery_datetime = models.DateTimeField(null=True, blank=True)
    fieldid = models.IntegerField(null=True, blank=True)
    subimage = models.IntegerField(null=True, blank=True)
    mount = models.IntegerField(null=True, blank=True)
    camera = models.IntegerField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    reference = models.CharField(max_length=150, null=True, blank=True)
    reference_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ref_cutout_filename = models.CharField(max_length=255, null=True, blank=True)
    new_cutout_filename = models.CharField(max_length=255, null=True, blank=True)
    diff_cutout_filename = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return self.candidate.name
