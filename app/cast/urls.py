from django.urls import path

from cast import views


urlpatterns = [
    path("external-api-status/", views.external_api_status_view, name="external-api-status"),
]
