from django.urls import path
from . import views

app_name = 'LAST'  # Namespacing for the app


urlpatterns = [
    path('observed-fields/', views.observed_fields_plot_view, name='observed-fields'),
    path('observed-fields/<str:night>/', views.observed_fields_plot_view, name='observed-fields_plot_by_date'),
]