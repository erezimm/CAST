"""
Constants and permission definitions for the Candidates module.

This module centralizes all permission strings and other constants used
throughout the Candidates application to ensure consistency and easy maintenance.
"""

# Permission constants
CAN_VIEW_CANDIDATES = 'candidates.can_view_candidates'

# Auth groups whose members are allowed to send TNS reports and perform other
# LAST general candidate actions. A list so additional groups can be granted
# access later without touching the views or templates.
TNS_REPORT_GROUPS = ['LAST general']

# Permission descriptions (for documentation and admin interface)
PERMISSION_DESCRIPTIONS = {
    CAN_VIEW_CANDIDATES: 'Can view and interact with the Candidates module',
}

# View names that require the can_view_candidates permission
PROTECTED_VIEWS = [
    'candidate_list_view',
    'update_followup_view', 
    'send_tns_report_view',
    'tns_report_view',
]

# Navigation items that require permission checks
NAVIGATION_ITEMS = {
    'candidates_menu': CAN_VIEW_CANDIDATES,
}