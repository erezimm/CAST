from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from candidates.services.tns_public_catalog import match_candidates_to_tns_catalog


class Command(BaseCommand):
    help = "Match candidates to TNS public catalog entries using local CSV only (no API requests)."

    def add_arguments(self, parser):
        parser.add_argument("--csv-path", type=str, default=None, help="Override input CSV path.")
        parser.add_argument(
            "--radius-arcsec",
            type=float,
            default=None,
            help="Match radius in arcseconds (defaults to settings value).",
        )
        parser.add_argument(
            "--overwrite-existing",
            action="store_true",
            help="Also rematch candidates that already have a tns_name.",
        )
        parser.add_argument(
            "--limit-candidates",
            type=int,
            default=None,
            help="Limit number of candidate rows processed (useful for testing).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute matches but do not write updates to the database.",
        )

    def handle(self, *args, **options):
        try:
            summary = match_candidates_to_tns_catalog(
                csv_path=options["csv_path"],
                radius_arcsec=options["radius_arcsec"],
                overwrite_existing=options["overwrite_existing"],
                limit_candidates=options["limit_candidates"],
                dry_run=options["dry_run"],
            )
        except Exception as exc:
            raise CommandError(f"Failed to match candidates against TNS catalog: {exc}") from exc

        success_text = "Dry run completed." if summary.dry_run else "TNS matching completed."
        self.stdout.write(self.style.SUCCESS(success_text))
        self.stdout.write(f"CSV: {summary.csv_path}")
        self.stdout.write(f"Candidates considered: {summary.candidates_considered}")
        self.stdout.write(f"Candidates matched: {summary.candidates_matched}")
        self.stdout.write(f"Candidates updated: {summary.candidates_updated}")
        self.stdout.write(f"Catalog rows scanned: {summary.rows_scanned}")
