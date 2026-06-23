from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class ObservedFieldsPlotViewTests(TestCase):
    @patch("LAST.views.get_sunset_sunrise")
    @patch("LAST.views.plot_fields")
    def test_observed_fields_uses_media_plot_url(self, mock_plot_fields, mock_get_sunset_sunrise):
        mock_get_sunset_sunrise.return_value = (None, None)

        response = self.client.get(reverse("LAST:observed-fields"), {"night": "2026-03-12"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plot_url"], "/data/LAST/plots/2026-03-12.png")
        self.assertContains(response, '/data/LAST/plots/2026-03-12.png')
        mock_plot_fields.assert_called_once_with(date_str="2026-03-12")
