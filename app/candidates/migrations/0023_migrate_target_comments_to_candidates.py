from math import acos, cos, radians, sin

from django.db import migrations


MATCH_RADIUS_ARCSEC = 3
MATCH_RADIUS_DEG = MATCH_RADIUS_ARCSEC / 3600.0


def angular_distance_deg(ra1, dec1, ra2, dec2):
    value = (
        sin(radians(dec1)) * sin(radians(dec2))
        + cos(radians(dec1)) * cos(radians(dec2)) * cos(radians(ra1 - ra2))
    )
    value = max(-1.0, min(1.0, value))
    return acos(value) * 180.0 / 3.141592653589793


def find_candidate_for_target(Candidate, target):
    nearby_candidates = Candidate.objects.filter(
        ra__gte=target.ra - MATCH_RADIUS_DEG,
        ra__lte=target.ra + MATCH_RADIUS_DEG,
        dec__gte=target.dec - MATCH_RADIUS_DEG,
        dec__lte=target.dec + MATCH_RADIUS_DEG,
    )

    best_candidate = None
    best_distance = None
    for candidate in nearby_candidates.iterator():
        distance = angular_distance_deg(target.ra, target.dec, candidate.ra, candidate.dec)
        if distance <= MATCH_RADIUS_DEG and (best_distance is None or distance < best_distance):
            best_candidate = candidate
            best_distance = distance

    return best_candidate


def migrate_target_comments_to_candidates(apps, schema_editor):
    Comment = apps.get_model("django_comments", "Comment")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Candidate = apps.get_model("candidates", "Candidate")
    Target = apps.get_model("tom_targets", "BaseTarget")

    target_content_type, _ = ContentType.objects.get_or_create(
        app_label="tom_targets",
        model="basetarget",
    )
    candidate_content_type, _ = ContentType.objects.get_or_create(
        app_label="candidates",
        model="candidate",
    )

    comments = Comment.objects.filter(content_type_id=target_content_type.id)

    target_ids = []
    for object_pk in comments.values_list("object_pk", flat=True).distinct():
        try:
            target_ids.append(int(object_pk))
        except (TypeError, ValueError):
            continue

    targets_by_id = Target.objects.in_bulk(target_ids)

    for comment in comments.iterator():
        try:
            target_id = int(comment.object_pk)
        except (TypeError, ValueError):
            continue

        target = targets_by_id.get(target_id)
        if target is None:
            continue

        candidate = find_candidate_for_target(Candidate, target)
        if candidate is None:
            continue

        comment.content_type_id = candidate_content_type.id
        comment.object_pk = str(candidate.id)
        comment.save(update_fields=["content_type", "object_pk"])


class Migration(migrations.Migration):

    dependencies = [
        ("candidates", "0022_candidate_too_name_candidate_classification_and_more"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("django_comments", "0004_add_object_pk_is_removed_index"),
        ("tom_targets", "0030_alter_basetarget_slope"),
    ]

    operations = [
        migrations.RunPython(migrate_target_comments_to_candidates, migrations.RunPython.noop),
    ]
