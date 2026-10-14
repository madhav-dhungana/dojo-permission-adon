from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from dojo.models import Product, Product_Type

from dojo_permission.services import provision_policy_group


class Command(BaseCommand):
    help = "Create or update a restricted Writer delegation group."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--description", default="")
        parser.add_argument("--product", action="append", type=int, default=[])
        parser.add_argument("--product-type", action="append", type=int, default=[])
        parser.add_argument("--member")

    def handle(self, *args, **options):
        products = tuple(Product.objects.filter(id__in=options["product"]))
        product_types = tuple(Product_Type.objects.filter(id__in=options["product_type"]))
        if len(products) != len(set(options["product"])):
            raise CommandError("One or more requested product IDs do not exist.")
        if len(product_types) != len(set(options["product_type"])):
            raise CommandError("One or more requested product type IDs do not exist.")

        member = None
        if options["member"]:
            try:
                member = get_user_model().objects.get(username=options["member"])
            except get_user_model().DoesNotExist as exc:
                raise CommandError(f"User {options['member']} does not exist.") from exc

        policy = provision_policy_group(
            name=options["name"],
            description=options["description"],
            products=products,
            product_types=product_types,
            initial_member=member,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Restricted group policy {policy.id} is ready for group {policy.group.name}.",
        ))
