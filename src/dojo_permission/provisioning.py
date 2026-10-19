"""Provision the library-owned restricted group without broadening access."""

from dataclasses import dataclass
import logging

from crum import get_current_user
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connections, transaction
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
    System_Settings,
)

from dojo_permission.models import RestrictedGroupPolicy

logger = logging.getLogger(__name__)

DEFAULT_MANAGED_GROUP_NAME = "Dev Team group"
LEGACY_MANAGED_GROUP_NAME = "Restricted Finding Editors"
DEFAULT_MANAGED_GROUP_DESCRIPTION = (
    "Managed by dojo-permission as a deny-only restriction marker."
)
PRODUCT_MANAGED_GROUP_DESCRIPTION_PREFIX = (
    "Managed by dojo-permission for exactly one product: "
)


class ManagedGroupCollision(RuntimeError):
    """An existing object occupies a library-managed identity."""


class ProvisioningUnavailable(RuntimeError):
    """A requested legacy managed product scope is not available."""


@dataclass(frozen=True)
class LegacyCleanupResult:
    """Outcome of conservatively retiring obsolete product-bound groups."""

    retired_group_ids: tuple[int, ...]
    preserved_policy_ids: tuple[int, ...]

    @property
    def retired_count(self) -> int:
        return len(self.retired_group_ids)

    @property
    def preserved_count(self) -> int:
        return len(self.preserved_policy_ids)


def managed_product_group_name(product: Product) -> str:
    return f"{DEFAULT_MANAGED_GROUP_NAME} - Product {product.pk}"


def legacy_managed_product_group_name(product: Product) -> str:
    return f"{LEGACY_MANAGED_GROUP_NAME} - Product {product.pk}"


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


def _rename_legacy_managed_group(
    group: Dojo_Group,
    *,
    legacy_name: str,
    new_name: str,
) -> None:
    # Only replace the former generated label on an already-owned group.
    # Preserve administrator-chosen names, memberships, and authorization IDs.
    if group.name != legacy_name:
        return
    if Dojo_Group.objects.filter(name=new_name).exclude(pk=group.pk).exists():
        raise ManagedGroupCollision(
            f"Managed group identity {new_name!r} is occupied by another group.",
        )
    group.name = new_name
    group.save(update_fields=("name",))


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
        _rename_legacy_managed_group(
            group,
            legacy_name=LEGACY_MANAGED_GROUP_NAME,
            new_name=DEFAULT_MANAGED_GROUP_NAME,
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


@transaction.atomic
def ensure_product_policy(product: Product) -> RestrictedGroupPolicy:
    """Resolve and validate an existing legacy product-bound policy.

    Version 0.3 intentionally provisions only the shared deny-only marker.
    This resolver remains for callers that must work with a product-bound
    policy created by an earlier release, but it never creates or repairs one.
    """

    product = Product.objects.select_for_update().get(pk=product.pk)
    product_policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .filter(product=product)
        .first()
    )
    if product_policy is None:
        raise ProvisioningUnavailable(
            f"No legacy product-bound restricted policy exists for product {product.pk}.",
        )
    group = Dojo_Group.objects.select_for_update().get(pk=product_policy.group_id)
    _validate_managed_group(group, display_name=group.name)
    if product_policy.is_default:
        raise ManagedGroupCollision(
            f"Product {product.pk} policy is marked as the default policy.",
        )

    assignments = list(Product_Group.objects.select_for_update().filter(group=group))
    if (
        len(assignments) != 1
        or assignments[0].product_id != product.pk
        or assignments[0].role_id != Roles.Writer
    ):
        raise ManagedGroupCollision(
            f"Legacy product policy {product_policy.pk} does not have exactly one "
            "Writer assignment for its product.",
        )
    return product_policy


def _legacy_product_policy_blockers(
    policy: RestrictedGroupPolicy,
    group: Dojo_Group,
) -> list[str]:
    """Return every reason a legacy group cannot be safely retired."""

    product = policy.product
    blockers = []
    if policy.is_default:
        blockers.append("policy is marked as the default")
    if not policy.is_active:
        blockers.append("policy is inactive")
    if policy.created_by_id is not None:
        blockers.append("policy has an explicit creator")
    if group.name not in {
        managed_product_group_name(product),
        legacy_managed_product_group_name(product),
    }:
        blockers.append("group name is not a generated product-group name")
    if group.description != managed_product_group_description(product):
        blockers.append("group description is not the generated description")
    if group.social_provider:
        blockers.append("group uses a social provider")
    if group.auth_group_id is None:
        blockers.append("group has no authorization group")
    else:
        if group.auth_group.permissions.exists():
            blockers.append("authorization group has configuration permissions")
        if group.auth_group.user_set.exists():
            blockers.append("authorization group has users")
    if Dojo_Group_Member.objects.filter(group=group).exists():
        blockers.append("group has members")
    if Global_Role.objects.filter(group=group, role__isnull=False).exists():
        blockers.append("group has a global role")
    if Product_Type_Group.objects.filter(group=group).exists():
        blockers.append("group has a product-type assignment")
    if System_Settings.objects.filter(default_group=group).exists():
        blockers.append("group is configured as the system default group")

    assignments = list(Product_Group.objects.select_for_update().filter(group=group))
    if (
        len(assignments) != 1
        or assignments[0].product_id != product.pk
        or assignments[0].role_id != Roles.Writer
    ):
        blockers.append("group does not have exactly one Writer assignment for its product")
    return blockers


@transaction.atomic
def retire_empty_generated_product_policies() -> LegacyCleanupResult:
    """Delete only untouched, empty product groups generated by older releases."""

    retired_group_ids = []
    preserved_policy_ids = []
    policies = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .filter(product__isnull=False)
        .select_related("product")
        .order_by("pk")
    )
    for policy in policies:
        group = Dojo_Group.objects.select_for_update().get(pk=policy.group_id)
        if group.auth_group_id is not None:
            # Lock the authorization row as well as the Dojo group before the
            # empty-membership checks and delete.
            group.auth_group = Group.objects.select_for_update().get(
                pk=group.auth_group_id,
            )
        blockers = _legacy_product_policy_blockers(policy, group)
        if blockers:
            preserved_policy_ids.append(policy.pk)
            logger.warning(
                "Preserving legacy dojo-permission product policy %s (group %s): %s.",
                policy.pk,
                group.pk,
                "; ".join(blockers),
            )
            continue

        group_id = group.pk
        group.delete()
        retired_group_ids.append(group_id)
        logger.info(
            "Retired empty legacy dojo-permission product group %s for product %s.",
            group_id,
            policy.product_id,
        )

    return LegacyCleanupResult(
        retired_group_ids=tuple(retired_group_ids),
        preserved_policy_ids=tuple(preserved_policy_ids),
    )


@transaction.atomic
def reconcile_managed_groups() -> tuple[RestrictedGroupPolicy, LegacyCleanupResult]:
    """Ensure the shared marker and retire only safe obsolete product groups."""

    default_policy = ensure_default_managed_group()
    cleanup = retire_empty_generated_product_policies()
    return default_policy, cleanup


def provision_existing_products() -> tuple[RestrictedGroupPolicy, ...]:
    """Compatibility shim: reconcile the single marker and create no products."""

    reconcile_managed_groups()
    return ()


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
        System_Settings._meta.db_table,
        Group._meta.db_table,
        Group.permissions.through._meta.db_table,
        get_user_model().groups.through._meta.db_table,
    }
    existing_tables = set(connections[using].introspection.table_names())
    if not required_tables.issubset(existing_tables):
        logger.warning(
            "Skipping dojo-permission provisioning because its required tables are unavailable.",
        )
        return
    with connections[using].cursor() as cursor:
        policy_columns = {
            column.name
            for column in connections[using].introspection.get_table_description(
                cursor,
                RestrictedGroupPolicy._meta.db_table,
            )
        }
    required_policy_columns = {
        "id",
        "group_id",
        "product_id",
        "is_default",
        "is_active",
        "created_by_id",
    }
    if not required_policy_columns.issubset(policy_columns):
        logger.warning(
            "Skipping dojo-permission provisioning because its policy schema is incomplete.",
        )
        return
    reconcile_managed_groups()


def register_provisioning_signals(app_config) -> None:
    # Disconnect the pre-0.3 receiver if an unusual test/reload process loaded
    # both implementations. New processes never connect a Product receiver.
    post_save.disconnect(
        sender=Product,
        dispatch_uid="dojo_permission.provision_product",
    )
    post_migrate.connect(
        _post_migrate_provision,
        sender=app_config,
        dispatch_uid="dojo_permission.provision_after_migrate",
        weak=False,
    )
