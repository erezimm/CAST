# CAST Permissions Reference

This document serves as a central reference for all custom permissions implemented in the CAST application.

## Overview

CAST uses Django's built-in permission system to manage access control. This allows for flexible, group-based permissions that can be managed through the Django admin interface.

## Custom Permissions

### Candidates Module

#### `candidates.can_view_candidates`
- **Description**: Allows users to view and interact with the Candidates module
- **Scope**: Candidates list, detail views, TNS reports, follow-up management
- **Views Protected**:
  - `candidate_list_view` - Main candidates listing page
  - `update_followup_view` - Mark/unmark candidates for follow-up
  - `send_tns_report_view` - Send TNS reports for candidates
  - `tns_report_view` - View TNS report details
- **Navigation**: Controls visibility of Candidates menu in navbar
- **Default Access**: Granted to superusers automatically

## Django Built-in Permissions

### User Management
- `auth.view_user` - View user list (admin interface)
- `auth.add_user` - Create new users (admin interface)
- `auth.change_user` - Modify user details (admin interface)
- `auth.delete_user` - Delete users (admin interface)

### Group Management
- `auth.view_group` - View groups (admin interface)
- `auth.add_group` - Create new groups (admin interface)
- `auth.change_group` - Modify group details (admin interface)
- `auth.delete_group` - Delete groups (admin interface)

## Permission Management

### Through Django Admin
1. Navigate to Django Admin interface
2. Go to "Groups" section
3. Create or edit groups as needed
4. Assign permissions to groups
5. Add users to appropriate groups

### Through Code (for development)
```python
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

# Create a new group
group = Group.objects.create(name='LAST Team')

# Get the permission
permission = Permission.objects.get(codename='can_view_candidates')

# Add permission to group
group.permissions.add(permission)

# Add user to group
user.groups.add(group)
```

## Best Practices

### Creating New Permissions
1. **Naming Convention**: Use `app_name.action_description` format
2. **Documentation**: Add to this file when creating new permissions
3. **Signal Handler**: Create signal handlers for automatic permission creation
4. **View Decorators**: Use `@permission_required('app.permission_name')` decorator

### Example: Adding a New Permission

#### 1. Create Signal Handler
```python
# In app/signals.py
@receiver(post_migrate)
def create_custom_permissions(sender, **kwargs):
    if sender.name == 'your_app':
        content_type = ContentType.objects.get_for_model(Permission)
        Permission.objects.get_or_create(
            codename='can_do_something',
            name='Can do something special',
            content_type=content_type,
        )
```

#### 2. Update App Configuration
```python
# In app/apps.py
def ready(self):
    import your_app.signals  # noqa
```

#### 3. Apply to Views
```python
# In views.py
@permission_required('your_app.can_do_something', raise_exception=True)
def your_view(request):
    # Your view logic here
    pass
```

#### 4. Update Navigation (if needed)
```html
<!-- In template -->
{% if request.user.is_superuser or request.user.has_perm('your_app.can_do_something') %}
    <!-- Show menu item -->
{% endif %}
```

## Troubleshooting

### Permission Not Working
1. Check if permission exists in Django admin
2. Verify user is assigned to correct group
3. Ensure signal handler ran after migration
4. Check view decorator syntax

### Permission Not Visible in Admin
1. Verify signal handler is importing correctly
2. Check app configuration `ready()` method
3. Run migrations if needed
4. Restart Django development server

### Navigation Not Showing
1. Check template logic for permission check
2. Verify user has correct permissions
3. Ensure superuser fallback is working

## Security Considerations

- Always use `raise_exception=True` in permission decorators
- Never hardcode group names in views
- Use Django's built-in permission system for consistency
- Regularly audit permissions in production
- Remove unused permissions and groups

## Future Enhancements

- Consider implementing object-level permissions for more granular control
- Add permission-based API endpoints
- Implement permission caching for performance
- Create permission audit logs