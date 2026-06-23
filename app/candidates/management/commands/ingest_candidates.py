import os

from django.core.management.base import BaseCommand
from django.conf import settings

from candidates.ingestion import process_multiple_json_files


class Command(BaseCommand):
    help = 'Ingest multiple JSON files as candidates'

    def add_arguments(self, parser):
        # Adding an optional argument for cutoff
        parser.add_argument(
            '--cutoff',
            type=int,
            default=3,
            help='Max number of days to process'
        )

    def handle(self, *args, **options):
        # Retrieve the cutoff from the options dictionary
        cutoff = options['cutoff']
        directory = os.path.join(settings.TRANSIENT_DIR, "json")

        try:
            # Pass the cutoff to your ingestion function
            total_candidates_added = process_multiple_json_files(
                directory,
                cutoff=cutoff
            )
            self.stdout.write(self.style.SUCCESS(f"Total candidates added: {total_candidates_added}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing files: {e}"))