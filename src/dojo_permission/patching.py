from functools import wraps

from crum import get_current_user
from django.db.models import Q

from dojo.authorization.roles_permissions import Permissions

_installed = False
_original_user_has_permission = None
_original_configuration_permission = None

USER_DIRECTORY_PERMISSIONS = frozenset({
    "auth.view_user", "auth.add_user", "auth.change_user", "auth.delete_user",
})


def restricted_configuration_permission(user, permission):
    from dojo_permission.policy import is_restricted_user

    if permission in USER_DIRECTORY_PERMISSIONS and is_restricted_user(user):
        return False
    return _original_configuration_permission(user, permission)


def restricted_user_has_permission(user, obj, permission):
    if user and not user.is_anonymous and user.is_superuser:
        return _original_user_has_permission(user, obj, permission)

    from dojo_permission.policy import shared_policy_denies_object_scope

    # Apply the direct-product boundary before permission normalization. This
    # keeps customized permission codes from bypassing the shared policy.
    if shared_policy_denies_object_scope(user, obj):
        return False

    allowed = _original_user_has_permission(user, obj, permission)
    if not allowed:
        return False

    from dojo_permission.policy import policy_denies_permission

    try:
        normalized_permission = permission if isinstance(permission, Permissions) else Permissions(permission)
    except (TypeError, ValueError):
        # Customized deployments may define additional permissions. Their
        # existing authorization result remains authoritative for those codes.
        return allowed
    return not policy_denies_permission(user, obj, normalized_permission)


def _actor_from_call(args, kwargs, user_position):
    actor = None
    if user_position is not None:
        actor = kwargs.get("user")
        if actor is None and len(args) > user_position:
            actor = args[user_position]
    return actor if actor is not None else get_current_user()


def _install_direct_product_queryset_scope(
    module,
    name,
    product_lookups,
    *,
    user_position=None,
):
    """Intersect a native authorization queryset with direct memberships."""
    original = getattr(module, name)
    if getattr(original, "_dojo_permission_direct_product_scope", False):
        return

    @wraps(original)
    def scoped(*args, **kwargs):
        result = original(*args, **kwargs)
        actor = _actor_from_call(args, kwargs, user_position)

        from dojo_permission.policy import (
            directly_assigned_product_ids,
            is_shared_restricted_user,
        )

        if not is_shared_restricted_user(actor):
            return result
        if isinstance(product_lookups, str):
            lookups = (product_lookups,)
        else:
            lookups = product_lookups
        product_ids = directly_assigned_product_ids(actor)
        scope = Q(**{f"{lookups[0]}__in": product_ids})
        for lookup in lookups[1:]:
            scope |= Q(**{f"{lookup}__in": product_ids})
        return result.filter(scope).distinct()

    scoped._dojo_permission_direct_product_scope = True

    from dojo_permission.privacy import replace_imported_references

    replace_imported_references(original, scoped)
    setattr(module, name, scoped)


def _install_explicit_product_argument_scope(
    module,
    name,
    product_lookup,
    *,
    product_position,
):
    """Preserve a native product argument even when its global-role path does not."""
    original = getattr(module, name)

    @wraps(original)
    def scoped(*args, **kwargs):
        result = original(*args, **kwargs)
        product = kwargs.get("product")
        if product is None and len(args) > product_position:
            product = args[product_position]

        from dojo_permission.policy import is_shared_restricted_user

        if product is None or not is_shared_restricted_user(get_current_user()):
            return result
        return result.filter(**{product_lookup: product}).distinct()

    from dojo_permission.privacy import replace_imported_references

    replace_imported_references(original, scoped)
    setattr(module, name, scoped)


def _install_shared_empty_queryset_scope(module, name, *, user_position=None):
    """Hide aggregate parent objects that cannot be safely product-scoped."""
    original = getattr(module, name)

    @wraps(original)
    def scoped(*args, **kwargs):
        result = original(*args, **kwargs)
        actor = _actor_from_call(args, kwargs, user_position)

        from dojo_permission.policy import is_shared_restricted_user

        return result.none() if is_shared_restricted_user(actor) else result

    from dojo_permission.privacy import replace_imported_references

    replace_imported_references(original, scoped)
    setattr(module, name, scoped)


def _install_direct_product_query_overlays() -> None:
    from dojo.cred import queries as cred_queries
    from dojo.endpoint import queries as endpoint_queries
    from dojo.engagement import queries as engagement_queries
    from dojo.finding import queries as finding_queries
    from dojo.finding_group import queries as finding_group_queries
    from dojo.jira import queries as jira_queries
    from dojo.location import queries as location_queries
    from dojo.product import queries as product_queries
    from dojo.product_type import queries as product_type_queries
    from dojo.risk_acceptance import queries as risk_acceptance_queries
    from dojo.test import queries as test_queries
    from dojo.tool_product import queries as tool_product_queries

    query_scopes = (
        # The native product query is the source for most UI selectors.
        (product_queries, "get_authorized_products", "pk", 1),
        (product_queries, "get_authorized_product_members", "product_id", None),
        (product_queries, "get_authorized_product_members_for_user", "product_id", None),
        (product_queries, "get_authorized_product_groups", "product_id", None),
        (product_queries, "get_authorized_app_analysis", "product_id", None),
        (product_queries, "get_authorized_languages", "product_id", None),
        (product_queries, "get_authorized_engagement_presets", "product_id", None),
        (
            product_queries,
            "get_authorized_product_api_scan_configurations",
            "product_id",
            None,
        ),
        (
            product_queries,
            "get_authorized_dojo_meta",
            (
                "product_id",
                "endpoint__product_id",
                "finding__test__engagement__product_id",
                "location__products__product_id",
                "location__findings__finding__test__engagement__product_id",
            ),
            None,
        ),
        # These native query helpers implement authorization independently of
        # get_authorized_products, so they need the same upper bound.
        (engagement_queries, "get_authorized_engagements", "product_id", None),
        (test_queries, "get_authorized_tests", "engagement__product_id", None),
        (
            test_queries,
            "get_authorized_test_imports",
            "test__engagement__product_id",
            None,
        ),
        (finding_queries, "get_authorized_findings", "test__engagement__product_id", 1),
        (
            finding_queries,
            "get_authorized_findings_for_queryset",
            "test__engagement__product_id",
            2,
        ),
        (
            finding_queries,
            "get_authorized_stub_findings",
            "test__engagement__product_id",
            None,
        ),
        (
            finding_queries,
            "get_authorized_vulnerability_ids",
            "finding__test__engagement__product_id",
            1,
        ),
        (
            finding_queries,
            "get_authorized_vulnerability_ids_for_queryset",
            "finding__test__engagement__product_id",
            2,
        ),
        (
            finding_group_queries,
            "get_authorized_finding_groups",
            "test__engagement__product_id",
            1,
        ),
        (
            finding_group_queries,
            "get_authorized_finding_groups_for_queryset",
            "test__engagement__product_id",
            2,
        ),
        (endpoint_queries, "get_authorized_endpoints", "product_id", 1),
        (endpoint_queries, "get_authorized_endpoints_for_queryset", "product_id", 2),
        (
            endpoint_queries,
            "get_authorized_endpoint_status",
            "endpoint__product_id",
            1,
        ),
        (
            endpoint_queries,
            "get_authorized_endpoint_status_for_queryset",
            "endpoint__product_id",
            2,
        ),
        (
            cred_queries,
            "get_authorized_cred_mappings",
            (
                "product_id",
                "engagement__product_id",
                "test__engagement__product_id",
                "finding__test__engagement__product_id",
            ),
            None,
        ),
        (
            cred_queries,
            "get_authorized_cred_mappings_for_queryset",
            (
                "product_id",
                "engagement__product_id",
                "test__engagement__product_id",
                "finding__test__engagement__product_id",
            ),
            None,
        ),
        (
            risk_acceptance_queries,
            "get_authorized_risk_acceptances",
            "engagement__product_id",
            None,
        ),
        (
            tool_product_queries,
            "get_authorized_tool_product_settings",
            "product_id",
            None,
        ),
        (
            jira_queries,
            "get_authorized_jira_projects",
            ("product_id", "engagement__product_id"),
            1,
        ),
        (
            jira_queries,
            "get_authorized_jira_issues",
            (
                "engagement__product_id",
                "finding_group__test__engagement__product_id",
                "finding__test__engagement__product_id",
            ),
            None,
        ),
        (
            location_queries,
            "get_authorized_locations",
            (
                "products__product_id",
                "findings__finding__test__engagement__product_id",
            ),
            2,
        ),
        (
            location_queries,
            "get_authorized_location_finding_reference",
            "finding__test__engagement__product_id",
            2,
        ),
        (
            location_queries,
            "get_authorized_location_product_reference",
            "product_id",
            2,
        ),
    )
    for module, name, product_lookups, user_position in query_scopes:
        _install_direct_product_queryset_scope(
            module,
            name,
            product_lookups,
            user_position=user_position,
        )
    _install_explicit_product_argument_scope(
        test_queries,
        "get_authorized_tests",
        "engagement__product",
        product_position=1,
    )
    _install_shared_empty_queryset_scope(
        product_type_queries,
        "get_authorized_product_types",
    )


def install_authorization_overlay() -> None:
    global _installed, _original_user_has_permission, _original_configuration_permission
    if _installed:
        return

    from dojo.authorization import authorization

    _original_user_has_permission = authorization.user_has_permission
    _original_configuration_permission = authorization.user_has_configuration_permission
    authorization.user_has_permission = restricted_user_has_permission
    authorization.user_has_configuration_permission = restricted_configuration_permission

    from dojo_permission.privacy import replace_imported_references

    replace_imported_references(_original_user_has_permission, restricted_user_has_permission)
    replace_imported_references(_original_configuration_permission, restricted_configuration_permission)

    from dojo.api_v2 import permissions as api_permissions
    from dojo.templatetags import authorization_tags

    api_permissions.user_has_permission = restricted_user_has_permission
    authorization_tags.user_has_permission = restricted_user_has_permission
    _install_direct_product_query_overlays()
    _installed = True
