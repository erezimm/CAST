from candidates.models.candidate import Candidate
from django.db import models


def candidate_data_product_path(instance, filename):
    return f'candidates/{instance.candidate.name}/{filename}'


class CandidateDataProduct(models.Model):
    candidate = models.ForeignKey(
        "candidates.Candidate",
        on_delete=models.CASCADE,
        related_name='data_products',
        null=False
    )
    datafile = models.FileField(upload_to=candidate_data_product_path, blank=True, null=True)
    data_product_type = models.CharField(max_length=50,
                                         choices=[('ref', 'ref'), ('new', 'new'), ('diff', 'diff'), ('ps1', 'ps1'),
                                                  ('sdss', 'sdss'), ('json', 'json'), ('tns_report', 'tns_report')])
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # File cleanup on delete is handled centrally by the post_delete signal
    # (candidates/models/signals.py), which covers direct, queryset, and cascade deletes.

    def __str__(self):
        return f"{self.name} (Candidate: {self.candidate.name})"
