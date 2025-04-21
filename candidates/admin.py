from django.contrib import admin
from .models import Candidate

@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'ra', 'dec', 'discovery_datetime', 'classification', 'created_at')
    list_filter = ('classification',)
    search_fields = ('name',)