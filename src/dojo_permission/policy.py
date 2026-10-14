from dojo.authorization.roles_permissions import Permissions, Roles
from dojo.models import (
    Dojo_Group_Member,
    Global_Role,
    Product,
    Product_Group,
    Product_Member,
    Product_Type_Group,
    Product_Type_Member,
)

from dojo_permission.models import RestrictedGroupPolicy

RESTRICTED_PERMISSIONS = frozenset({
    Permissions.Engagement_Add,
    Permissions.Engagement_Delete,
    Permissions.Test_Add,
    Permissions.Finding_Add,
    Permissions.Import_Scan_Result,
    Permissions.Finding_Delete,
})

RESTRICTED_MEMBER_ROLES = frozenset({
    Roles.Reader,
    Roles.Writer,
    Roles.Maintainer,
})

PRIVILEGED_ROLES = frozenset({Roles.Maintainer, Roles.Owner})


def product_for_object(obj) -> Product | None:
    model_name = getattr(getattr(obj, "_meta", None), "model_name", None)
    if model_name == "product":
        return obj
    if model_name == "engagement":
        return obj.product
    if model_name == "test":
        return obj.engagement.product
    if model_name in {"finding", "stub_finding"}:
        return obj.test.engagement.product
    if model_name in {"product_member", "product_group"}:
        return obj.product
    return None


def _has_privileged_role(role_ids) -> bool:
    return any(role_id in PRIVILEGED_ROLES for role_id in role_ids)


def _active_policy_group_ids() -> set[int]:
    return set(
        RestrictedGroupPolicy.objects.filter(is_active=True)
        .values_list("group_id", flat=True)
    )


def _restricted_group_ids_for_user_and_product(user, product: Product) -> set[int]:
    active_policy_ids = _active_policy_group_ids()
    if not active_policy_ids:
        return set()

    owner_group_ids = set(
        Dojo_Group_Member.objects.filter(
            user=user,
            group_id__in=active_policy_ids,
            role_id=Roles.Owner,
        ).values_list("group_id", flat=True)
    )
    memberships = Dojo_Group_Member.objects.filter(
        user=user,
        group_id__in=active_policy_ids,
        role_id__in=RESTRICTED_MEMBER_ROLES,
    ).values_list("group_id", flat=True)
    member_group_ids = set(memberships).difference(owner_group_ids)
    if not member_group_ids:
        return set()

    product_group_ids = Product_Group.objects.filter(
        product=product,
        group_id__in=member_group_ids,
        role_id=Roles.Writer,
    ).values_list("group_id", flat=True)
    product_type_group_ids = Product_Type_Group.objects.filter(
        product_type=product.prod_type,
        group_id__in=member_group_ids,
        role_id=Roles.Writer,
    ).values_list("group_id", flat=True)
    return set(product_group_ids).union(product_type_group_ids)


def _has_independent_privileged_role(user, product: Product, excluded_group_ids: set[int]) -> bool:
    global_role = Global_Role.objects.filter(user=user).select_related("role").first()
    if global_role and global_role.role_id in PRIVILEGED_ROLES:
        return True

    group_global_roles = Global_Role.objects.filter(
        group__users=user,
    ).exclude(group_id__in=excluded_group_ids).values_list("role_id", flat=True)
    if _has_privileged_role(group_global_roles):
        return True

    direct_product_roles = Product_Member.objects.filter(
        user=user,
        product=product,
    ).values_list("role_id", flat=True)
    if _has_privileged_role(direct_product_roles):
        return True

    direct_product_type_roles = Product_Type_Member.objects.filter(
        user=user,
        product_type=product.prod_type,
    ).values_list("role_id", flat=True)
    if _has_privileged_role(direct_product_type_roles):
        return True

    other_product_group_roles = Product_Group.objects.filter(
        product=product,
        group__users=user,
    ).exclude(group_id__in=excluded_group_ids).values_list("role_id", flat=True)
    if _has_privileged_role(other_product_group_roles):
        return True

    other_product_type_group_roles = Product_Type_Group.objects.filter(
        product_type=product.prod_type,
        group__users=user,
    ).exclude(group_id__in=excluded_group_ids).values_list("role_id", flat=True)
    return _has_privileged_role(other_product_type_group_roles)


def policy_denies_permission(user, obj, permission: Permissions) -> bool:
    if permission not in RESTRICTED_PERMISSIONS:
        return False
    if not user or user.is_anonymous or user.is_superuser:
        return False

    product = product_for_object(obj)
    if product is None:
        return False

    restricted_group_ids = _restricted_group_ids_for_user_and_product(user, product)
    if not restricted_group_ids:
        return False

    return not _has_independent_privileged_role(
        user,
        product,
        excluded_group_ids=restricted_group_ids,
    )
