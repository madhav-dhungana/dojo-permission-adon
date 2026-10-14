import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dojo", "0264_alter_url_identity_hash_alter_urlevent_identity_hash"),
    ]

    operations = [
        migrations.CreateModel(
            name="RestrictedGroupPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_dojo_permission_policies",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "group",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dojo_permission_policy",
                        to="dojo.dojo_group",
                    ),
                ),
            ],
            options={
                "verbose_name": "restricted group policy",
                "verbose_name_plural": "restricted group policies",
                "ordering": ("group__name",),
            },
        ),
    ]
