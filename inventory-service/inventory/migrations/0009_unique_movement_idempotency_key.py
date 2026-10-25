from django.db import migrations, models
from django.db.models import Count


def mark_duplicate_keys(apps, schema_editor):
    """Older retries could record a movement twice: keep the first, tag the rest so the index can be built."""
    StockMovement = apps.get_model("inventory", "StockMovement")
    dupes = (
        StockMovement.objects.exclude(idempotency_key="")
        .values("type", "idempotency_key").annotate(n=Count("id")).filter(n__gt=1)
    )
    for d in dupes:
        later = StockMovement.objects.filter(type=d["type"], idempotency_key=d["idempotency_key"]).order_by("id")[1:]
        for m in later:
            m.idempotency_key = f"{m.idempotency_key[:230]}#dup-{m.pk}"
            m.save(update_fields=["idempotency_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_default_reorder_points"),
    ]

    operations = [
        migrations.RunPython(mark_duplicate_keys, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("type", "idempotency_key"),
                name="uniq_movement_type_idempotency_key",
            ),
        ),
    ]
