import json
from io import BytesIO
from unittest.mock import MagicMock, call, mock_open, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from candidates.ingestion import IngestionResult, has_lasair_credentials, process_json_file, process_multiple_json_files
from candidates.models import Candidate
from candidates.photometry_utils import get_lasair_api_token, get_ztf_fp
from cast.external_api_status import (
    EXTERNAL_API_STATUS_CACHE_KEY,
    check_atlas_status,
    check_lasair_status,
    check_tns_status,
    get_external_api_status_snapshot,
    refresh_external_api_status,
)


class IngestionRegressionTests(TestCase):
    def test_candidate_with_extreme_coordinates_saves_generated_name(self):
        candidate = Candidate.objects.create(
            ra=359.9999583333333,
            dec=89.99999722222222,
            discovery_datetime=timezone.now(),
        )

        self.assertTrue(candidate.name.startswith("LAST J"))
        self.assertLessEqual(len(candidate.name), 150)

    @override_settings(LASAIR_API_KEY="")
    def test_has_lasair_credentials_false_without_api_key(self):
        self.assertFalse(has_lasair_credentials())

    @override_settings(BROKERS={"LASAIR": {}})
    def test_get_ztf_fp_returns_none_without_credentials(self):
        candidate = Candidate.objects.create(
            ra=123.456,
            dec=-45.678,
            discovery_datetime=timezone.now(),
        )

        self.assertIsNone(get_ztf_fp(candidate))

    def test_process_json_file_returns_three_values_on_parse_failure(self):
        mock_file = BytesIO(b"invalid json content")
        mock_file.name = "invalid_file.json"

        count, result, candidate_name = process_json_file(mock_file)

        self.assertEqual(count, 0)
        self.assertEqual(result, IngestionResult.PARSE_FAILED)
        self.assertIsNone(candidate_name)

    @patch("candidates.ingestion.try_associate_host_galaxy")
    @patch("candidates.ingestion.try_forced_photometry")
    @patch("candidates.ingestion.try_add_cutout")
    @patch("candidates.ingestion.update_candidate_cutouts")
    @patch("candidates.ingestion.add_ToO_names_to_candidate")
    @patch("candidates.ingestion.add_photometry_from_last_report")
    @patch("candidates.ingestion.handle_candidate_identity")
    def test_process_json_file_returns_candidate_name_when_created(
        self,
        mock_handle_candidate_identity,
        _mock_add_photometry,
        _mock_add_too_names,
        _mock_update_cutouts,
        mock_try_add_cutout,
        mock_try_forced_photometry,
        mock_try_associate_host_galaxy,
    ):
        candidate = Candidate.objects.create(
            ra=123.456,
            dec=-45.678,
            discovery_datetime=timezone.now(),
        )
        mock_handle_candidate_identity.return_value = (candidate, True)

        mock_data = {
            "at_report": {
                "RA": {"value": 123.456},
                "Dec": {"value": -45.678},
                "discovery_datetime": ["2026-02-26 15:00:00 UTC"],
            },
            "last_report": {},
        }
        mock_file = BytesIO(json.dumps(mock_data).encode())
        mock_file.name = "valid_file.json"

        count, result, candidate_name = process_json_file(mock_file, lasair_enabled=False)

        self.assertEqual((count, result, candidate_name), (1, IngestionResult.CREATED, candidate.name))
        self.assertEqual(mock_try_add_cutout.call_count, 2)
        mock_try_forced_photometry.assert_called_once()
        mock_try_associate_host_galaxy.assert_called_once_with(candidate)

    @patch("candidates.ingestion.process_json_file")
    @patch("candidates.ingestion.has_lasair_credentials")
    @patch("candidates.ingestion.collect_new_json")
    @patch("candidates.ingestion.get_json_names_from_db")
    def test_process_multiple_json_files_checks_lasair_credentials_once(
        self,
        mock_get_json_names_from_db,
        mock_collect_new_json,
        mock_has_lasair_credentials,
        mock_process_json_file,
    ):
        mock_get_json_names_from_db.return_value = set()
        mock_collect_new_json.return_value = ["/tmp/a.json", "/tmp/b.json"]
        mock_has_lasair_credentials.return_value = False
        mock_process_json_file.side_effect = [
            (1, IngestionResult.CREATED, "LAST J000000.00+000000.00"),
            (0, IngestionResult.PARSE_FAILED, None),
        ]

        with patch("builtins.open", mock_open(read_data=b"{}")):
            total = process_multiple_json_files("/tmp/input", cutoff=1)

        self.assertEqual(total, 1)
        mock_has_lasair_credentials.assert_called_once_with()
        self.assertEqual(len(mock_process_json_file.call_args_list), 2)
        self.assertTrue(all(args.args[1] is False for args in mock_process_json_file.call_args_list))


class ExternalApiStatusTests(TestCase):
    def setUp(self):
        cache.delete(EXTERNAL_API_STATUS_CACHE_KEY)
        self.superuser = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass",
        )
        self.user = get_user_model().objects.create_user(
            username="user",
            email="user@example.com",
            password="pass",
        )

    def test_external_api_status_requires_superuser(self):
        response = self.client.get(reverse("external-api-status"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.user)
        response = self.client.get(reverse("external-api-status"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.superuser)
        with patch("cast.views.refresh_external_api_status") as mock_refresh:
            mock_refresh.return_value = {"refreshed_at": "now", "services": []}
            response = self.client.get(reverse("external-api-status"))
        self.assertEqual(response.status_code, 200)

    @patch("cast.views.refresh_external_api_status")
    def test_external_api_status_page_refreshes_when_cache_is_empty(self, mock_refresh):
        mock_refresh.return_value = {"refreshed_at": "now", "services": [{"name": "TNS"}]}
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("external-api-status"))

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_called_once_with()
        self.assertContains(response, "TNS")

    @patch("cast.views.refresh_external_api_status")
    def test_external_api_status_page_uses_cached_snapshot(self, mock_refresh):
        cache.set(EXTERNAL_API_STATUS_CACHE_KEY, {"refreshed_at": "cached", "services": [{"name": "LASAIR"}]}, None)
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("external-api-status"))

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_not_called()
        self.assertContains(response, "LASAIR")

    @patch("cast.views.refresh_external_api_status")
    def test_external_api_status_manual_refresh_overwrites_cache(self, mock_refresh):
        mock_refresh.return_value = {"refreshed_at": "new", "services": [{"name": "ATLAS"}]}
        self.client.force_login(self.superuser)

        response = self.client.post(reverse("external-api-status"))

        self.assertEqual(response.status_code, 200)
        mock_refresh.assert_called_once_with()
        self.assertContains(response, "ATLAS")

    @override_settings(BROKERS={"TNS": {"api_key": "", "bot_id": "", "bot_name": ""}}, TNS_TEST=True)
    def test_tns_status_reports_not_configured(self):
        status = check_tns_status()

        self.assertFalse(status["configured"])
        self.assertIsNone(status["auth_ok"])
        self.assertEqual(status["metadata"]["environment"], "sandbox")

    @override_settings(BROKERS={"TNS": {"api_key": "key", "bot_id": "1", "bot_name": "bot"}}, TNS_TEST=False)
    @patch("cast.external_api_status.requests.post")
    def test_tns_status_reports_success_and_prod_environment(self, mock_post):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": []}
        mock_post.return_value = response

        status = check_tns_status()

        self.assertTrue(status["configured"])
        self.assertTrue(status["auth_ok"])
        self.assertEqual(status["metadata"]["environment"], "production")

    @override_settings(BROKERS={"LASAIR": {"api_key": "token"}}, LASAIR_API_KEY="")
    @patch("cast.external_api_status.lasair_client")
    def test_lasair_status_uses_normalized_token_source(self, mock_client):
        mock_client.return_value.cone.return_value = {"object": "ZTF"}

        status = check_lasair_status()

        self.assertTrue(status["configured"])
        self.assertTrue(status["auth_ok"])
        mock_client.assert_called_once()
        self.assertEqual(mock_client.call_args.args[0], "token")

    @override_settings(BROKERS={"ATLAS": {"api_token": "secret"}})
    @patch("cast.external_api_status.requests.get")
    def test_atlas_status_reports_auth_failure(self, mock_get):
        response = MagicMock()
        response.status_code = 401
        mock_get.return_value = response

        status = check_atlas_status()

        self.assertTrue(status["configured"])
        self.assertFalse(status["auth_ok"])
        self.assertEqual(status["status_label"], "auth_failed")

    @patch("cast.external_api_status.build_external_api_status_snapshot")
    def test_refresh_external_api_status_writes_cache(self, mock_build):
        mock_build.return_value = {"refreshed_at": "stored", "services": [{"name": "TNS"}]}

        snapshot = refresh_external_api_status()

        self.assertEqual(snapshot["refreshed_at"], "stored")
        self.assertEqual(get_external_api_status_snapshot()["refreshed_at"], "stored")

    @override_settings(BROKERS={"LASAIR": {"api_key": "broker-token"}}, LASAIR_API_KEY="")
    def test_get_lasair_api_token_prefers_broker_value(self):
        self.assertEqual(get_lasair_api_token(), "broker-token")
        self.assertTrue(has_lasair_credentials())

    @override_settings(BROKERS={"LASAIR": {}}, LASAIR_API_KEY="legacy-token")
    def test_get_lasair_api_token_falls_back_to_legacy_setting(self):
        self.assertEqual(get_lasair_api_token(), "legacy-token")
        self.assertTrue(has_lasair_credentials())

    @override_settings(BROKERS={"TNS": {"api_key": "key", "bot_id": "1", "bot_name": "bot"}}, TNS_TEST=True)
    @patch("cast.external_api_status.requests.post", side_effect=requests.Timeout("boom"))
    def test_tns_status_reports_network_error(self, _mock_post):
        status = check_tns_status()

        self.assertTrue(status["configured"])
        self.assertFalse(status["auth_ok"])
        self.assertEqual(status["status_label"], "network_error")
