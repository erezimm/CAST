from django.contrib import admin
from .models import Candidate

@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'ra', 'dec', 'discovery_datetime', 'real_bogus', 'created_at')
    list_filter = ('real_bogus',)
    search_fields = ('name',)