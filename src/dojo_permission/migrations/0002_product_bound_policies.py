import django.db.models.deletion
from django.db import migrations, models


def _replace_product_fk_delete_action(apps, schema_editor, *, action):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    policy_model = apps.get_model("dojo_permission", "RestrictedGroupPolicy")
    product_model = apps.get_model("dojo", "Product")
    table_name = policy_model._meta.db_table
    column_name = policy_model._meta.get_field("product").column
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)
    matching_names = [
        name
        for name, details in constraints.items()
        if details.get("foreign_key") and details.get("columns") == [column_name]
    ]
    if len(matching_names) != 1:
        raise RuntimeError(
            "Could not uniquely identify dojo-permission's product foreign key.",
        )

    quote = schema_editor.quote_name
    constraint_name = matching_names[0]
    schema_editor.execute(
        f"ALTER TABLE {quote(table_name)} DROP CONSTRAINT {quote(constraint_name)}",
    )
    schema_editor.execute(
        f"ALTER TABLE {quote(table_name)} "
        f"ADD CONSTRAINT {quote(constraint_name)} "
        f"FOREIGN KEY ({quote(column_name)}) "
        f"REFERENCES {quote(product_model._meta.db_table)} "
        f"({quote(product_model._meta.pk.column)}) "
        f"ON DELETE {action} DEFERRABLE INITIALLY DEFERRED",
    )


def set_product_fk_cascade(apps, schema_editor):
    _replace_product_fk_delete_action(
        apps,
        schema_editor,
        action="CASCADE",
    )


def restore_product_fk_no_action(apps, schema_editor):
    _replace_product_fk_delete_action(
        apps,
        schema_editor,
        action="NO ACTION",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("dojo_permission", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="restrictedgrouppolicy",
            name="group",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="dojo_permission_policy",
                to="dojo.dojo_group",
            ),
        ),
        migrations.AddField(
            model_name="restrictedgrouppolicy",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="dojo_permission_policies",
                to="dojo.product",
            ),
        ),
        migrations.AddField(
            model_name="restrictedgrouppolicy",
            name="is_default",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddConstraint(
            model_name="restrictedgrouppolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(product__isnull=False),
                fields=("product",),
                name="dojo_permission_unique_product_policy",
            ),
        ),
        migrations.AddConstraint(
            model_name="restrictedgrouppolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_default=True),
                fields=("is_default",),
                name="dojo_permission_unique_default_policy",
            ),
        ),
        migrations.RunPython(
            set_product_fk_cascade,
            reverse_code=restore_product_fk_no_action,
        ),
    ]
