# CAST Changes

## 2026-06-22
- Require authentication on all candidate state-changing views (delete, real/bogus, classification, refresh ATLAS/ZTF, set-reported, add target, Astro-COLIBRI, upload, cutouts), which were previously reachable by anonymous users under the `READ_ONLY` auth strategy
- Validate candidate action `return_url` values against the current host to prevent open redirects
- Fix the ATLAS forced-photometry request payload (RA/Dec/mjd_min were wrapped in set literals, so the query was malformed)
- Stop `Candidate.save()` from performing blocking TNS lookups by default; the lookup is now opt-in (`check_tns=True`) and only used on initial ingest
- Fix observation and discovery times being dropped when ingesting AT-format reports: the `" UTC"` suffix on report datetimes made parsing fail and store null timestamps; `ensure_aware_utc` now understands the suffix and is used consistently across the ingestion paths
- Harden photometry time handling: convert ATLAS/ZTF forced-photometry timestamps explicitly to UTC instead of relying on `TIME_ZONE`, and correct the `last_report` ingestion defaults (`{}` → `[]`) so a missing detections list no longer raises a swallowed error
- Add a "LegacySurvey Viewer" action button to each candidate that opens the Legacy Survey sky viewer (with DESI EDR/DR1 spectra) at the candidate's coordinates in a new tab, useful where SDSS SkyServer lacks coverage (implements https://github.com/erezimm/CAST/issues/57)

## 2026-06-21
- Replace the TNS report group authorization gate so unauthorized users get a clear "you do not have permission" message and a disabled, tooltip-explained button instead of being redirected into an unusable re-authentication loop
- Make the TNS reporting authorization configurable via a `TNS_REPORT_GROUPS` list so additional auth groups can be granted access without code changes, shared as a single source of truth between views and templates
- Require the submitting user to have a first and last name set before sending a TNS report, with an actionable message instead of an `IndexError` during reporter-name formatting
- Move the hardcoded TNS reporter list and special name-formatting cases out of the Python code into a loadable `candidates/config/tns_reporters.json` file (path overridable via `TNS_REPORTERS_CONFIG_PATH`)

## 2026-03-18
- Add admin external API status dashboard with dedicated CAST URLs, template, navbar access, and startup monitoring integration
- Disable unavailable candidate actions in the UI based on external API availability and add coverage for action-state behavior
- Unify candidate comments into a single canonical discussion thread shared with target detail pages and move candidate comments beneath survey cutouts with HTMX posting support
- Automatically create or reuse a target when marking a candidate as real and add migration support for moving target comments into candidate discussions
- Switch ATLAS authentication from username/password to API key configuration and update environment examples, tests, and status checks accordingly
- Fix observed fields plot media URL handling and add regression tests for the LAST observed fields view
- Filter GLADE host-galaxy redshifts using the distance reliability flag before propagating values to candidates
- Move the about page template into the CAST app template directory and remove the old duplicate template path

## 2026-02-26
- Implement custom permission-based authentication system for Candidates module: replace hardcoded group-based access with flexible permission system using `candidates.can_view_candidates` permission
- Create permission signals in app/candidates/signals.py for automatic permission creation during app initialization
- Update 4 candidate views to use `@permission_required('candidates.can_view_candidates', raise_exception=True)` decorators instead of group checks
- Modify navigation template to use new permission system with `has_candidates_permission` template filter
- Move candidate templates from app/templates/candidates/ to app/candidates/templates/candidates/ for better organization
- Remove redundant base_candidates.html template and enhance base template structure with responsive layout
- Add django_gravatar to INSTALLED_APPS for user profile images and implement AlpineJS support for enhanced frontend interactivity
- Add CSRF token handling for htmx requests and theme support with get_theme template tag
- Restructure Candidates module with modular architecture: split models.py into dedicated files (alert.py, candidate.py, data_product.py, photometry.py)
- Create new service layer at app/candidates/services/ for business logic separation (enrichment.py, identity.py, parsing.py, tns_public_catalog.py)
- Add new management commands: download_tns_public_objects.py, match_candidates_to_tns.py
- Reorganize static files: proper structure for LAST and CAST modules with custom CSS, JS, and branding assets
- Update ClickHouse dependency from clickhouse-connect to clickhouse_connect
- Add comprehensive CAST candidates configuration in settings.py
- Enhance TNS integration with improved cone search and object matching capabilities
- Improve static file handling with Django finders integration
- Update import paths to reflect new module structure
