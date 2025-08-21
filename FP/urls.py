from django.urls import path
from . import views

app_name = 'FP'  # Namespacing for the app

urlpatterns = [
    path('last-fp/', views.force_photometry_view, name='forced_photometry'),
    path('download-fp-results/', views.download_fp_csv, name='download_fp_csv'),
]