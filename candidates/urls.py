from django.urls import path
from . import views

app_name = 'candidates'  # Namespacing for the app

urlpatterns = [
    # path('add/', views.add_candidate_view, name='add'),  # Add a single candidate
    path('list/', views.candidate_list_view, name='list'),  # List all candidates
    path('<int:candidate_id>/', views.candidate_detail, name='candidate_detail'),
    path('upload/', views.upload_file_view, name='upload'),  # Upload candidates via a file
    path('delete/', views.delete_candidate_view, name='delete_candidate'),  # URL for deletion
    path('add_target/', views.add_target_view, name='add_target'),  # URL for Add Target
    # path('cone_search/', views.cone_search_view, name='cone_search'),  # Cone search URL
    path('update/<int:candidate_id>/', views.update_real_bogus_view, name='update_real_bogus'),
    path('send_tns/<int:candidate_id>/', views.send_tns_report_view, name='send_tns_report'),
    path('tns_report/<int:candidate_id>/', views.tns_report_view, name='tns_report_details'),
    path('update_cutouts/<int:candidate_id>/', views.update_cutouts_view, name='update_candidate_cutouts')
    
]