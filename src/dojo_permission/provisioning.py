"""Provision the library-owned restricted groups without broadening access."""

from functools import wraps
import logging

from crum import get_current_user
from django.contrib.auth.models import Group
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction
from django.db.models.signals import post_migrate, post_save

# Importing this module registers DefectDojo's Dojo_Group lifecycle receivers.
from dojo.group import utils as _dojo_group_utils  # noqa: F401
from dojo.authorization.roles_permissions import Roles
from dojo.models import (
    Dojo_Group,
    Dojo_Group_Member,
    Global_Role,
    Product,
    Product_Group,
    Product_Type_Group,
    Role,
)

from dojo_permission.models import RestrictedGroupPolicy

logger = logging.getLogger(__name__)

DEFAULT_MANAGED_GROUP_NAME = "Restricted Finding Editors"
DEFAULT_MANAGED_GROUP_DESCRIPTION = (
    "Managed by dojo-permission as a deny-only restriction marker."
)
PRODUCT_MANAGED_GROUP_DESCRIPTION_PREFIX = (
    "Managed by dojo-permission for exactly one product: "
)


class ManagedGroupCollision(RuntimeError):
    """An existing object occupies a library-managed identity."""


class ProvisioningUnavailable(RuntimeError):
    """DefectDojo's required role fixtures are not available yet."""


def managed_product_group_name(product: Product) -> str:
    return f"{DEFAULT_MANAGED_GROUP_NAME} - Product {product.pk}"


def managed_product_group_description(product: Product) -> str:
    return f"{PRODUCT_MANAGED_GROUP_DESCRIPTION_PREFIX}{product.pk}."


def is_default_managed_policy(policy: RestrictedGroupPolicy) -> bool:
    return policy.is_default


def _remove_automatic_creator_owner(group: Dojo_Group, *, group_created: bool) -> None:
    if not group_created:
        return
    actor = get_current_user()
    if actor is None or getattr(actor, "is_anonymous", True) or not actor.pk:
        return
    Dojo_Group_Member.objects.filter(
        group=group,
        user=actor,
        role__is_owner=True,
    ).delete()


def _managed_group(*, name: str, description: str) -> tuple[Dojo_Group, bool]:
    group = Dojo_Group.objects.select_for_update().filter(name=name).first()
    created = False
    if group is None:
        group = Dojo_Group.objects.create(name=name, description=description)
        created = True
        _remove_automatic_creator_owner(group, group_created=True)
    elif group.description != description or group.social_provider:
        raise ManagedGroupCollision(
            f"Refusing to adopt existing DefectDojo group {name!r}.",
        )

    _validate_managed_group(group, display_name=name)
    return group, created


def _validate_managed_group(group: Dojo_Group, *, display_name: str) -> None:
    if group.social_provider:
        raise ManagedGroupCollision(
            f"Managed DefectDojo group {display_name!r} uses a social provider.",
        )
    if group.auth_group_id is None:
        raise ManagedGroupCollision(
            f"Managed DefectDojo group {display_name!r} has no authorization group.",
        )
    if group.auth_group.permissions.exists():
        raise ManagedGroupCollision(
            f"Managed DefectDojo group {display_name!r} has configuration permissions.",
        )
    # Native group administration writes a neutral row with role=NULL. It
    # carries no global access, so only a non-null role is unsafe here.
    if Global_Role.objects.filter(group=group, role__isnull=False).exists():
        raise ManagedGroupCollision(
            f"Managed DefectDojo group {display_name!r} has a global role.",
        )
    if Product_Type_Group.objects.filter(group=group).exists():
        raise ManagedGroupCollision(
            f"Managed DefectDojo group {display_name!r} has a product-type assignment.",
        )


def _activate_managed_policy(policy: RestrictedGroupPolicy) -> RestrictedGroupPolicy:
    if not policy.is_active:
        policy.is_active = True
        policy.save(update_fields=("is_active", "updated_at"))
    return policy


@transaction.atomic
def ensure_default_managed_group() -> RestrictedGroupPolicy:
    policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .filter(is_default=True)
        .first()
    )
    if policy is not None:
        group = Dojo_Group.objects.select_for_update().get(pk=policy.group_id)
        _validate_managed_group(group, display_name=group.name)
        if policy.product_id is not None:
            raise ManagedGroupCollision(
                "The default managed restriction marker is bound to a product.",
            )
        if Product_Group.objects.filter(group=group).exists():
            raise ManagedGroupCollision(
                "The default managed restriction marker must not grant product access.",
            )
        return _activate_managed_policy(policy)

    group, group_created = _managed_group(
        name=DEFAULT_MANAGED_GROUP_NAME,
        description=DEFAULT_MANAGED_GROUP_DESCRIPTION,
    )
    assignments = Product_Group.objects.filter(group=group)
    if assignments.exists():
        raise ManagedGroupCollision(
            "The default managed restriction marker must not grant product access.",
        )

    if not group_created:
        raise ManagedGroupCollision(
            "Refusing to adopt a pre-existing group as the default restriction marker.",
        )
    policy = RestrictedGroupPolicy.objects.create(
        group=group,
        product=None,
        is_default=True,
    )
    if policy.product_id is not None:
        raise ManagedGroupCollision(
            "The default managed restriction marker is bound to a product.",
        )
    return _activate_managed_policy(policy)


def _writer_role() -> Role:
    try:
        return Role.objects.get(pk=Roles.Writer)
    except Role.DoesNotExist as exc:
        raise ProvisioningUnavailable(
            "DefectDojo's Writer role is not available yet.",
        ) from exc


@transaction.atomic
def ensure_product_policy(product: Product) -> RestrictedGroupPolicy:
    product = Product.objects.select_for_update().get(pk=product.pk)
    writer = _writer_role()
    group_name = managed_product_group_name(product)
    product_policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .filter(product=product)
        .first()
    )
    if product_policy is None:
        group, group_created = _managed_group(
            name=group_name,
            description=managed_product_group_description(product),
        )
    else:
        group = Dojo_Group.objects.select_for_update().get(pk=product_policy.group_id)
        if Dojo_Group.objects.filter(name=group_name).exclude(pk=group.pk).exists():
            raise ManagedGroupCollision(
                f"Managed group identity {group_name!r} is occupied by another group.",
            )
        # The product foreign key is the durable ownership marker. Native
        # administrators may rename this already-owned group without causing a
        # replacement group to be created on the next reconciliation.
        _validate_managed_group(group, display_name=group.name)

    group_policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .filter(group=group)
        .first()
    )
    if product_policy is None and not group_created and group_policy is None:
        raise ManagedGroupCollision(
            f"Refusing to adopt pre-existing DefectDojo group {group_name!r}.",
        )
    if product_policy is not None and product_policy.group_id != group.id:
        raise ManagedGroupCollision(
            f"Product {product.pk} already has a different restricted policy.",
        )
    if group_policy is not None and group_policy.product_id != product.pk:
        raise ManagedGroupCollision(
            f"Managed group {group_name!r} belongs to a different policy scope.",
        )
    if (product_policy is not None and product_policy.is_default) or (
        group_policy is not None and group_policy.is_default
    ):
        raise ManagedGroupCollision(
            f"Managed group {group_name!r} is marked as the default policy.",
        )

    policy = product_policy or group_policy
    if policy is None:
        policy = RestrictedGroupPolicy.objects.create(group=group, product=product)
    policy = _activate_managed_policy(policy)

    assignments = list(Product_Group.objects.select_for_update().filter(group=group))
    if not assignments:
        Product_Group.objects.create(product=product, group=group, role=writer)
    elif len(assignments) != 1 or assignments[0].product_id != product.pk or assignments[0].role_id != Roles.Writer:
        raise ManagedGroupCollision(
            f"Managed group {group_name!r} does not have exactly one Writer product assignment.",
        )
    return policy


def provision_existing_products() -> tuple[RestrictedGroupPolicy, ...]:
    ensure_default_managed_group()
    if not Role.objects.filter(pk=Roles.Writer).exists():
        logger.warning(
            "Skipping product-bound dojo-permission provisioning until the Writer role exists.",
        )
        return ()
    return tuple(ensure_product_policy(product) for product in Product.objects.all().iterator())


def _post_migrate_provision(*, using, **kwargs) -> None:
    if using != "default":
        logger.warning(
            "Skipping dojo-permission provisioning for unsupported database alias %s.",
            using,
        )
        return
    required_tables = {
        RestrictedGroupPolicy._meta.db_table,
        Dojo_Group._meta.db_table,
        Dojo_Group_Member._meta.db_table,
        Global_Role._meta.db_table,
        Product._meta.db_table,
        Product_Group._meta.db_table,
        Product_Type_Group._meta.db_table,
        Role._meta.db_table,
        Group._meta.db_table,
        Group.permissions.through._meta.db_table,
    }
    existing_tables = set(connections[using].introspection.table_names())
    if not required_tables.issubset(existing_tables):
        logger.warning(
            "Skipping dojo-permission provisioning because its required tables are unavailable.",
        )
        return
    provision_existing_products()


def _product_post_save(*, instance: Product, raw: bool, using: str, **kwargs) -> None:
    if raw or using != "default":
        return
    try:
        ensure_product_policy(instance)
    except ProvisioningUnavailable:
        logger.warning(
            "Product %s was not provisioned because the Writer role is unavailable.",
            instance.pk,
        )


def _install_atomic_product_save() -> None:
    current_save = Product.save
    if getattr(current_save, "_dojo_permission_atomic_provisioning", False):
        return

    @wraps(current_save)
    def atomic_product_save(instance, *args, **kwargs):
        using = (
            kwargs.get("using")
            or router.db_for_write(type(instance), instance=instance)
            or instance._state.db
            or DEFAULT_DB_ALIAS
        )
        if using != DEFAULT_DB_ALIAS:
            return current_save(instance, *args, **kwargs)
        # Product post-save provisioning now participates in the same database
        # transaction as the core Product INSERT/UPDATE. A collision therefore
        # cannot leave a newly created Product without its managed policy.
        with transaction.atomic(using=using):
            return current_save(instance, *args, **kwargs)

    atomic_product_save._dojo_permission_atomic_provisioning = True
    Product.save = atomic_product_save


def register_provisioning_signals(app_config) -> None:
    _install_atomic_product_save()
    post_migrate.connect(
        _post_migrate_provision,
        sender=app_config,
        dispatch_uid="dojo_permission.provision_after_migrate",
        weak=False,
    )
    post_save.connect(
        _product_post_save,
        sender=Product,
        dispatch_uid="dojo_permission.provision_product",
        weak=False,
    )
