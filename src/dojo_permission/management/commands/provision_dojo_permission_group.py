from django.core.management.base import BaseCommand, CommandError

from dojo.models import Product

from dojo_permission.provisioning import (
    ManagedGroupCollision,
    ProvisioningUnavailable,
    ensure_default_managed_group,
    ensure_product_policy,
)


class Command(BaseCommand):
    help = "Reconcile dojo-permission's default marker and product-bound groups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--product",
            action="append",
            type=int,
            default=[],
            help="Reconcile this product ID; repeat as needed. Omit for all products.",
        )

    def handle(self, *args, **options):
        requested_ids = set(options["product"])
        products = Product.objects.filter(pk__in=requested_ids) if requested_ids else Product.objects.all()
        if requested_ids:
            found_ids = set(products.values_list("pk", flat=True))
            missing_ids = requested_ids - found_ids
            if missing_ids:
                missing = ", ".join(str(pk) for pk in sorted(missing_ids))
                raise CommandError(f"Unknown product ID(s): {missing}.")

        try:
            default_policy = ensure_default_managed_group()
            product_policies = [ensure_product_policy(product) for product in products.iterator()]
        except (ManagedGroupCollision, ProvisioningUnavailable) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Restricted marker policy {default_policy.pk} and "
                f"{len(product_policies)} product policy/policies are ready.",
            ),
        )
