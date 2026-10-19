from django.core.management.base import BaseCommand, CommandError

from dojo_permission.provisioning import (
    ManagedGroupCollision,
    reconcile_managed_groups,
)


class Command(BaseCommand):
    help = (
        "Reconcile dojo-permission's shared marker and retire untouched legacy "
        "product groups."
    )

    def handle(self, *args, **options):
        try:
            default_policy, cleanup = reconcile_managed_groups()
        except ManagedGroupCollision as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Restricted marker policy {default_policy.pk} is ready for group "
                f"{default_policy.group.name!r}; retired {cleanup.retired_count} "
                "empty legacy product group(s) and preserved "
                f"{cleanup.preserved_count} legacy product policy group(s).",
            ),
        )
