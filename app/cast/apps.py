from django.apps import AppConfig
from django.urls import include, path


class CastConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cast'

    def ready(self):
        from cast.external_api_status import schedule_external_api_status_startup_refresh

        schedule_external_api_status_startup_refresh()

    def include_url_paths(self):
        return [
            path("", include("cast.urls")),
        ]
