from django.core.management.base import BaseCommand
from candidates.utils import process_multiple_json_files
from django.conf import settings

class Command(BaseCommand):
    help = 'Ingest multiple JSON files as candidates'

    def handle(self, *args, **kwargs):
        directory = settings.TRANSIENT_DIR+'json/'
        try:
            total_candidates_added = process_multiple_json_files(directory)
            self.stdout.write(self.style.SUCCESS(f"Total candidates added: {total_candidates_added}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing files: {e}"))