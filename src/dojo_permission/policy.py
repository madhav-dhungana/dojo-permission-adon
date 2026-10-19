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
    Permissions.Engagement_Edit,
    Permissions.Engagement_Delete,
    Permissions.Test_Add,
    Permissions.Finding_Add,
    Permissions.Import_Scan_Result,
    Permissions.Finding_Delete,
    Permissions.Product_Delete,
    Permissions.Product_Group_Add,
    Permissions.Product_Group_Edit,
    Permissions.Product_Group_Delete,
    Permissions.Product_Manage_Members,
    Permissions.Product_Member_Delete,
    Permissions.Product_Member_Add_Owner,
    Permissions.Product_Type_Manage_Members,
    Permissions.Product_Type_Member_Delete,
    Permissions.Product_Type_Member_Add_Owner,
    Permissions.Product_Type_Group_Add,
    Permissions.Product_Type_Group_Edit,
    Permissions.Product_Type_Group_Delete,
})

RESTRICTED_GROUP_PERMISSIONS = frozenset({
    Permissions.Group_Edit,
    Permissions.Group_Delete,
    Permissions.Group_Manage_Members,
    Permissions.Group_Member_Delete,
    Permissions.Group_Add_Owner,
})

RESTRICTED_PRODUCT_TYPE_PERMISSIONS = frozenset({
    Permissions.Product_Type_Add_Product,
    Permissions.Product_Type_View,
    Permissions.Product_Type_Manage_Members,
    Permissions.Product_Type_Member_Delete,
    Permissions.Product_Type_Member_Add_Owner,
    Permissions.Product_Type_Edit,
    Permissions.Product_Type_Delete,
    Permissions.Product_Type_Add,
    Permissions.Product_Type_Group_View,
    Permissions.Product_Type_Group_Add,
    Permissions.Product_Type_Group_Add_Owner,
    Permissions.Product_Type_Group_Edit,
    Permissions.Product_Type_Group_Delete,
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
    if model_name in {
        "app_analysis",
        "endpoint",
        "engagement",
        "engagement_presets",
        "languages",
        "product_api_scan_configuration",
    }:
        return obj.product
    if model_name == "test":
        return obj.engagement.product
    if model_name in {"finding", "finding_group", "stub_finding"}:
        return obj.test.engagement.product
    if model_name in {"product_member", "product_group"}:
        return obj.product
    if model_name == "risk_acceptance":
        engagement = obj.engagement
        return engagement.product if engagement is not None else None
    if model_name == "cred_mapping":
        if obj.product_id is not None:
            return obj.product
        if obj.engagement_id is not None:
            return obj.engagement.product
        if obj.test_id is not None:
            return obj.test.engagement.product
        if obj.finding_id is not None:
            return obj.finding.test.engagement.product
    return None


def _has_privileged_role(role_ids) -> bool:
    return any(role_id in PRIVILEGED_ROLES for role_id in role_ids)


def restricted_membership_group_ids(user) -> set[int]:
    """Policy enrollment never changes DefectDojo's native access grants."""
    if not user or user.is_anonymous or user.is_superuser:
        return set()
    active_policy_ids = RestrictedGroupPolicy.objects.filter(is_active=True).values("group_id")
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
    return set(memberships).difference(owner_group_ids)


def shared_restricted_membership_group_ids(user) -> set[int]:
    """Active default-policy memberships that receive strict product scoping."""
    if not user or user.is_anonymous or user.is_superuser:
        return set()
    default_group_ids = RestrictedGroupPolicy.objects.filter(
        is_active=True,
        is_default=True,
    ).values("group_id")
    owner_group_ids = set(
        Dojo_Group_Member.objects.filter(
            user=user,
            group_id__in=default_group_ids,
            role_id=Roles.Owner,
        ).values_list("group_id", flat=True)
    )
    memberships = Dojo_Group_Member.objects.filter(
        user=user,
        group_id__in=default_group_ids,
        role_id__in=RESTRICTED_MEMBER_ROLES,
    ).values_list("group_id", flat=True)
    return set(memberships).difference(owner_group_ids)


def legacy_restricted_membership_group_ids(user) -> set[int]:
    """Restricted memberships attached to pre-0.3 nondefault policies."""
    if not user or user.is_anonymous or user.is_superuser:
        return set()
    legacy_group_ids = RestrictedGroupPolicy.objects.filter(
        is_active=True,
        is_default=False,
    ).values("group_id")
    owner_group_ids = set(
        Dojo_Group_Member.objects.filter(
            user=user,
            group_id__in=legacy_group_ids,
            role_id=Roles.Owner,
        ).values_list("group_id", flat=True)
    )
    memberships = Dojo_Group_Member.objects.filter(
        user=user,
        group_id__in=legacy_group_ids,
        role_id__in=RESTRICTED_MEMBER_ROLES,
    ).values_list("group_id", flat=True)
    return set(memberships).difference(owner_group_ids)


def _has_global_owner(user) -> bool:
    if Global_Role.objects.filter(user=user, role_id=Roles.Owner).exists():
        return True
    return Global_Role.objects.filter(
        group__users=user,
        role_id=Roles.Owner,
    ).exists()


def _owns_default_policy_group(user) -> bool:
    return Dojo_Group_Member.objects.filter(
        user=user,
        role_id=Roles.Owner,
        group__dojo_permission_policy__is_active=True,
        group__dojo_permission_policy__is_default=True,
    ).exists()


def is_shared_restricted_user(user) -> bool:
    """Whether the one shared policy applies its strict direct-product scope."""
    if not user or user.is_anonymous or user.is_superuser:
        return False
    if _has_global_owner(user) or _owns_default_policy_group(user):
        return False
    return bool(shared_restricted_membership_group_ids(user))


def is_restricted_user(user) -> bool:
    if not user or user.is_anonymous or user.is_superuser:
        return False
    if _has_global_owner(user) or _owns_default_policy_group(user):
        return False
    if shared_restricted_membership_group_ids(user):
        # The shared marker is deliberately not escaped by Maintainer roles on
        # products, product types, global assignments, or unrelated groups.
        return True

    group_ids = legacy_restricted_membership_group_ids(user)
    if not group_ids:
        return False
    if Global_Role.objects.filter(user=user, role_id__in=PRIVILEGED_ROLES).exists():
        return False
    return not Global_Role.objects.filter(
        group__users=user,
        role_id__in=PRIVILEGED_ROLES,
    ).exclude(group_id__in=group_ids).exists()


def directly_assigned_product_ids(user):
    """A lazy queryset containing the user's explicit Product_Member scope."""
    return Product_Member.objects.filter(user=user).values("product_id")


def _has_direct_product_assignment(user, product: Product) -> bool:
    return Product_Member.objects.filter(user=user, product=product).exists()


def _has_direct_product_owner(user, product: Product) -> bool:
    return Product_Member.objects.filter(
        user=user,
        product=product,
        role_id=Roles.Owner,
    ).exists()


def shared_policy_denies_object_scope(user, obj) -> bool:
    """Deny every product-backed operation outside direct membership scope."""
    if not is_shared_restricted_user(user):
        return False
    product = product_for_object(obj)
    return product is not None and not _has_direct_product_assignment(user, product)


def _legacy_restricted_group_ids_for_user_and_product(user, product: Product) -> set[int]:
    member_group_ids = legacy_restricted_membership_group_ids(user)
    return _policy_group_ids_for_product(member_group_ids, product, include_default=False)


def _policy_group_ids_for_product(
    group_ids,
    product: Product,
    *,
    include_default: bool = True,
) -> set[int]:
    if not group_ids:
        return set()

    policies = RestrictedGroupPolicy.objects.filter(is_active=True, group_id__in=group_ids)
    if not include_default:
        policies = policies.filter(is_default=False)
    explicit = set(policies.filter(product=product).values_list("group_id", flat=True))
    legacy_ids = set(policies.filter(product__isnull=True).values_list("group_id", flat=True))
    if not legacy_ids:
        return explicit
    product_links = Product_Group.objects.filter(group_id__in=legacy_ids)
    type_links = Product_Type_Group.objects.filter(group_id__in=legacy_ids)
    global_links = Global_Role.objects.filter(group_id__in=legacy_ids, role__isnull=False)
    linked_ids = set(product_links.values_list("group_id", flat=True))
    linked_ids.update(type_links.values_list("group_id", flat=True))
    linked_ids.update(global_links.values_list("group_id", flat=True))
    # An unlinked enrollment group marks existing native access; it grants none.
    marker_ids = legacy_ids.difference(linked_ids)
    return explicit.union(
        marker_ids,
        product_links.filter(product=product).values_list("group_id", flat=True),
        type_links.filter(product_type=product.prod_type).values_list("group_id", flat=True),
        global_links.values_list("group_id", flat=True),
    )


def _owns_applicable_policy(user, product: Product) -> bool:
    owner_group_ids = set(Dojo_Group_Member.objects.filter(
        user=user,
        role_id=Roles.Owner,
        group__dojo_permission_policy__is_active=True,
    ).values_list("group_id", flat=True))
    return bool(
        _policy_group_ids_for_product(
            owner_group_ids,
            product,
            include_default=False,
        ),
    )


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
    if not user or user.is_anonymous or user.is_superuser:
        return False
    if _has_global_owner(user) or _owns_default_policy_group(user):
        return False

    if permission in RESTRICTED_GROUP_PERMISSIONS:
        model_name = getattr(getattr(obj, "_meta", None), "model_name", None)
        group_id = obj.pk if model_name == "dojo_group" else getattr(obj, "group_id", None)
        return group_id in restricted_membership_group_ids(user)
    product = product_for_object(obj)
    if is_shared_restricted_user(user):
        if product is not None:
            if not _has_direct_product_assignment(user, product):
                return True
            if _has_direct_product_owner(user, product):
                return False
            return permission in RESTRICTED_PERMISSIONS

        model_name = getattr(getattr(obj, "_meta", None), "model_name", None)
        product_type_id = (
            obj.pk
            if model_name == "product_type"
            else getattr(obj, "product_type_id", None)
        )
        if (
            product_type_id is not None
            and permission in RESTRICTED_PRODUCT_TYPE_PERMISSIONS
        ):
            return True
        return False

    if permission not in RESTRICTED_PERMISSIONS:
        return False

    if product is None:
        model_name = getattr(getattr(obj, "_meta", None), "model_name", None)
        product_type_id = obj.pk if model_name == "product_type" else getattr(obj, "product_type_id", None)
        if product_type_id is not None and permission in RESTRICTED_PRODUCT_TYPE_PERMISSIONS:
            return any(
                policy_denies_permission(user, item, permission)
                for item in Product.objects.filter(prod_type_id=product_type_id).select_related("prod_type")
            )
        return False

    restricted_group_ids = _legacy_restricted_group_ids_for_user_and_product(
        user,
        product,
    )
    if not restricted_group_ids:
        return False
    if _owns_applicable_policy(user, product):
        return False

    return not _has_independent_privileged_role(
        user,
        product,
        excluded_group_ids=restricted_group_ids,
    )
