import logging

from candidates.models.data_product import CandidateDataProduct
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver
from django.db.models.signals import post_delete, post_migrate

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=CandidateDataProduct)
def delete_datafile(sender, instance, **kwargs):
    """
    Delete the file backing a CandidateDataProduct when its row is removed.

    This is the single source of truth for file cleanup: it fires on direct
    deletes, queryset deletes, and cascade deletes (when a Candidate is removed,
    its data products are CASCADE-deleted and each emits this signal). Uses the
    storage API so it works regardless of the storage backend.
    """
    if instance.datafile:
        try:
            instance.datafile.delete(save=False)
        except Exception as e:
            logger.warning(f"Error deleting file for data product {instance.pk}: {e}")


@receiver(post_migrate)
def create_candidates_permissions(sender, **kwargs):
    """
    Create custom permissions for the candidates app.
    This signal runs after migrations to ensure the permission exists.
    """
    if sender.name == 'candidates':
        content_type = ContentType.objects.get_for_model(Permission)

        # Create the can_view_candidates permission
        Permission.objects.get_or_create(
            codename='can_view_candidates',
            name='Can view candidates',
            content_type=content_type,
        )
