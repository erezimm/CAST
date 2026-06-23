from importlib import import_module

import pandas as pd
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_comments.models import Comment
from tom_targets.models import Target
from unittest.mock import patch

from candidates.gal_association import associate_galaxy
from candidates.models import Candidate
from candidates.utils import add_candidate_as_target
from candidates.views import render_candidate_row_response
from cast.external_api_status import EXTERNAL_API_STATUS_CACHE_KEY, write_external_api_status_snapshot
from django.core.cache import cache


class GladeAssociationTests(TestCase):
    @patch("candidates.gal_association.get_glade")
    def test_associate_galaxy_returns_redshift_only_for_spectroscopic_distance_flags(self, mock_get_glade):
        mock_get_glade.return_value = pd.DataFrame(
            [
                {
                    "GWGC": "GAL-PHOTO",
                    "HyperLEDA": None,
                    "2MASS": None,
                    "wiseX": None,
                    "SDSS-DR16Q": None,
                    "RA": 10.0,
                    "Dec": 20.0,
                    "d_L": 100.0,
                    "dist_flag": 1,
                    "z_helio": 0.123,
                },
                {
                    "GWGC": "GAL-SPEC",
                    "HyperLEDA": None,
                    "2MASS": None,
                    "wiseX": None,
                    "SDSS-DR16Q": None,
                    "RA": 30.0,
                    "Dec": 40.0,
                    "d_L": 200.0,
                    "dist_flag": 2,
                    "z_helio": 0.456,
                },
            ]
        )

        photo_name, photo_dist, photo_redshift = associate_galaxy(10.0, 20.0, radius=30.0)
        spec_name, spec_dist, spec_redshift = associate_galaxy(30.0, 40.0, radius=30.0)

        self.assertEqual((photo_name, photo_dist, photo_redshift), ("GAL-PHOTO (GWGC)", 100.0, None))
        self.assertEqual((spec_name, spec_dist, spec_redshift), ("GAL-SPEC (GWGC)", 200.0, 0.456))


class CandidateTargetAutomationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reviewer",
            password="pass",
            first_name="Ada",
            last_name="Lovelace",
        )
        self.client.force_login(self.user)

    def test_marking_real_creates_target(self):
        candidate = Candidate.objects.create(
            ra=12.345,
            dec=-45.678,
            discovery_datetime=timezone.now(),
        )

        response = self.client.post(
            reverse("candidates:update_real_bogus", args=[candidate.id]),
            {"real_bogus": "real", "return_url": reverse("candidates:list")},
        )

        self.assertEqual(response.status_code, 302)
        candidate.refresh_from_db()
        self.assertTrue(candidate.real_bogus)
        self.assertEqual(Target.objects.count(), 1)
        self.assertEqual(Target.objects.get().ra, candidate.ra)

    def test_marking_real_reuses_existing_target(self):
        candidate = Candidate.objects.create(
            ra=22.345,
            dec=-15.678,
            discovery_datetime=timezone.now(),
        )
        target = add_candidate_as_target(candidate.id)

        response = self.client.post(
            reverse("candidates:update_real_bogus", args=[candidate.id]),
            {"real_bogus": "real", "return_url": reverse("candidates:list")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Target.objects.count(), 1)
        self.assertEqual(Target.objects.get().id, target.id)

    def test_marking_bogus_does_not_create_target(self):
        candidate = Candidate.objects.create(
            ra=32.345,
            dec=-5.678,
            discovery_datetime=timezone.now(),
        )

        response = self.client.post(
            reverse("candidates:update_real_bogus", args=[candidate.id]),
            {"real_bogus": "bogus", "return_url": reverse("candidates:list")},
        )

        self.assertEqual(response.status_code, 302)
        candidate.refresh_from_db()
        self.assertFalse(candidate.real_bogus)
        self.assertEqual(Target.objects.count(), 0)


class CandidateActionConfigurationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.candidate = Candidate.objects.create(
            ra=12.0,
            dec=34.0,
            discovery_datetime=timezone.now(),
        )
        cache.delete(EXTERNAL_API_STATUS_CACHE_KEY)

    def _render_row(self):
        request = self.factory.get("/candidates/")
        request.user = AnonymousUser()
        with patch("candidates.views.build_candidate_status_item") as mock_build_item:
            mock_build_item.return_value = {
                "candidate": self.candidate,
                "target": None,
                "graph": None,
                "cutouts": [],
                "last_alert": None,
                "classification_choices": [],
            }
            response = render_candidate_row_response(request, self.candidate)
        return response.content.decode("utf-8")

    def test_row_uses_cached_external_api_status_to_disable_actions(self):
        write_external_api_status_snapshot(
            {
                "refreshed_at": "2026-03-12T00:00:00+00:00",
                "services": [
                    {
                        "name": "TNS",
                        "configured": True,
                        "auth_ok": False,
                        "status_label": "auth_failed",
                        "message": "TNS credentials were rejected.",
                        "checked_at": "2026-03-12T00:00:00+00:00",
                        "metadata": {"environment": "sandbox"},
                    },
                    {
                        "name": "Astro-COLIBRI",
                        "configured": False,
                        "auth_ok": None,
                        "status_label": "not_configured",
                        "message": "Service is not configured.",
                        "checked_at": "2026-03-12T00:00:00+00:00",
                        "metadata": {"check_mode": "best_effort"},
                    },
                ],
            }
        )

        content = self._render_row()

        self.assertIn('title="TNS credentials were rejected."', content)
        self.assertIn('title="Service is not configured."', content)
        self.assertNotIn(reverse("candidates:tns_report_details", args=[self.candidate.id]), content)
        self.assertNotIn(reverse("candidates:astro_colibri_report", args=[self.candidate.id]), content)

    @override_settings(
        BROKERS={"TNS": {"api_key": "key", "bot_id": "1", "bot_name": "bot"}},
        ASTRO_COLIBRI={"api_url": "https://astro-colibri.science", "username": "last", "password": "secret"},
    )
    def test_row_falls_back_to_configuration_when_no_cached_status_exists(self):
        content = self._render_row()

        self.assertIn(reverse("candidates:tns_report_details", args=[self.candidate.id]), content)
        self.assertIn(reverse("candidates:astro_colibri_report", args=[self.candidate.id]), content)
        self.assertNotIn('title="Service is not configured."', content)


class CandidateCommentThreadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass",
        )
        self.client.force_login(self.user)
        Site.objects.update_or_create(
            id=1,
            defaults={"domain": "example.com", "name": "example.com"},
        )

    def _create_comment(self, obj, text):
        Comment.objects.create(
            content_type=ContentType.objects.get_for_model(obj),
            object_pk=str(obj.pk),
            user=self.user,
            user_name=self.user.get_username(),
            user_email=self.user.email,
            comment=text,
            submit_date=timezone.now(),
            site_id=1,
        )

    def test_candidate_detail_renders_candidate_comments(self):
        candidate = Candidate.objects.create(
            ra=42.345,
            dec=5.678,
            discovery_datetime=timezone.now(),
        )
        self._create_comment(candidate, "Shared candidate thread")

        response = self.client.get(reverse("candidates:candidate_detail", args=[candidate.id]))

        self.assertContains(response, "Comments")
        self.assertContains(response, "Shared candidate thread")

    def test_candidate_detail_places_comments_under_survey_cutouts(self):
        candidate = Candidate.objects.create(
            ra=41.0,
            dec=6.0,
            discovery_datetime=timezone.now(),
        )

        response = self.client.get(reverse("candidates:candidate_detail", args=[candidate.id]))

        content = response.content.decode("utf-8")
        self.assertLess(content.index("Survey Cutouts"), content.index("Comments"))

    def test_htmx_comment_post_returns_updated_partial_without_redirect(self):
        candidate = Candidate.objects.create(
            ra=43.345,
            dec=7.678,
            discovery_datetime=timezone.now(),
        )

        response = self.client.post(
            reverse("candidates:candidate_comments", args=[candidate.id]),
            {"comment": "HTMX comment", "next": reverse("candidates:candidate_detail", args=[candidate.id])},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "HTMX comment")
        self.assertEqual(
            Comment.objects.filter(
                content_type=ContentType.objects.get_for_model(candidate),
                object_pk=str(candidate.pk),
            ).count(),
            1,
        )

    def test_regular_authenticated_user_can_post_comment(self):
        commenter = get_user_model().objects.create_user(
            username="commenter",
            password="pass",
            email="commenter@example.com",
        )
        self.client.force_login(commenter)
        candidate = Candidate.objects.create(
            ra=43.445,
            dec=7.778,
            discovery_datetime=timezone.now(),
        )

        response = self.client.post(
            reverse("candidates:candidate_comments", args=[candidate.id]),
            {"comment": "User comment", "next": reverse("candidates:candidate_detail", args=[candidate.id])},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User comment")

    def test_anonymous_user_cannot_post_comment(self):
        candidate = Candidate.objects.create(
            ra=44.345,
            dec=8.678,
            discovery_datetime=timezone.now(),
        )
        self.client.logout()

        response = self.client.post(
            reverse("candidates:candidate_comments", args=[candidate.id]),
            {"comment": "Blocked", "next": reverse("candidates:candidate_detail", args=[candidate.id])},
            HTTP_HX_REQUEST="true",
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(
            Comment.objects.filter(
                content_type=ContentType.objects.get_for_model(candidate),
                object_pk=str(candidate.pk),
            ).exists()
        )

    def test_anonymous_user_sees_sign_in_note_on_candidate_detail(self):
        candidate = Candidate.objects.create(
            ra=45.345,
            dec=9.678,
            discovery_datetime=timezone.now(),
        )
        self.client.logout()

        response = self.client.get(reverse("candidates:candidate_detail", args=[candidate.id]))

        self.assertContains(response, "Sign in to post a comment.")

    def test_target_detail_renders_matched_candidate_comments(self):
        candidate = Candidate.objects.create(
            ra=52.345,
            dec=15.678,
            discovery_datetime=timezone.now(),
        )
        target = add_candidate_as_target(candidate.id)
        self._create_comment(candidate, "Visible from target detail")

        response = self.client.get(reverse("targets:detail", kwargs={"pk": target.id}))

        self.assertContains(response, "Visible from target detail")

    def test_target_detail_shows_note_when_no_candidate_exists(self):
        target = Target.objects.create(
            name="Orphan target",
            type=Target.SIDEREAL,
            ra=200.0,
            dec=30.0,
        )

        response = self.client.get(reverse("targets:detail", kwargs={"pk": target.id}))

        self.assertContains(
            response,
            "Comments are candidate-backed and unavailable until a candidate exists for this target.",
        )

    def test_migration_moves_target_comments_to_matched_candidate(self):
        candidate = Candidate.objects.create(
            ra=62.345,
            dec=25.678,
            discovery_datetime=timezone.now(),
        )
        target = add_candidate_as_target(candidate.id)
        target_comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(target),
            object_pk=str(target.pk),
            user=self.user,
            user_name=self.user.get_username(),
            user_email=self.user.email,
            comment="Migrated thread",
            submit_date=timezone.now(),
            site_id=1,
        )

        from django.apps import apps as global_apps

        migration_module = import_module("candidates.migrations.0002_migrate_target_comments_to_candidates")
        migration_module.migrate_target_comments_to_candidates(global_apps, None)
        target_comment.refresh_from_db()

        self.assertEqual(target_comment.content_type, ContentType.objects.get_for_model(candidate))
        self.assertEqual(target_comment.object_pk, str(candidate.pk))
