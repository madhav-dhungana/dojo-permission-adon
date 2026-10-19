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
    _installed = True
