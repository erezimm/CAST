from django.core.management.base import BaseCommand
from django.conf import settings
from candidates.models import Candidate,CandidateDataProduct
from candidates.utils import fetch_sdss_cutout

class Command(BaseCommand):
    help = 'Update SDSS data for all candidates'
    # Loop through all candidates
    def handle(self, *args, **kwargs):
        for candidate in Candidate.objects.all():
            try:
                sdss_cutout = fetch_sdss_cutout(candidate.ra, candidate.dec)
            except Exception as e:
                print (f"Error fetching SDSS cutout for {candidate.name}: {e}")
            if sdss_cutout:
                CandidateDataProduct.objects.create(
                    candidate=candidate,
                    datafile=sdss_cutout,
                    data_product_type='sdss',
                    name=f"{candidate.name} SDSS cutout",
                )