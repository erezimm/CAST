from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from candidates.services.tns_public_catalog import download_tns_public_objects_catalog


class Command(BaseCommand):
    help = "Download the public TNS catalog ZIP and extract its CSV for offline matching."

    def add_arguments(self, parser):
        parser.add_argument("--url", type=str, default=None, help="Override TNS public ZIP URL.")
        parser.add_argument(
            "--user-agent",
            type=str,
            default=None,
            help='Override User-Agent (must include required tns_marker JSON).',
        )
        parser.add_argument("--zip-path", type=str, default=None, help="Override output ZIP path.")
        parser.add_argument("--csv-path", type=str, default=None, help="Override extracted CSV path.")
        parser.add_argument(
            "--timeout-seconds",
            type=int,
            default=300,
            help="HTTP timeout in seconds for the download request.",
        )

    def handle(self, *args, **options):
        try:
            summary = download_tns_public_objects_catalog(
                url=options["url"],
                user_agent=options["user_agent"],
                zip_path=options["zip_path"],
                csv_path=options["csv_path"],
                timeout_seconds=options["timeout_seconds"],
            )
        except Exception as exc:
            raise CommandError(f"Failed to download TNS public objects catalog: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("TNS public catalog download completed."))
        self.stdout.write(f"URL: {summary.url}")
        self.stdout.write(f"ZIP: {summary.zip_path}")
        self.stdout.write(f"CSV: {summary.csv_path}")
        self.stdout.write(f"Downloaded bytes: {summary.bytes_downloaded}")
