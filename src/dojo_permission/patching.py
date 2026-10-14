from dojo.authorization.roles_permissions import Permissions

_installed = False
_original_user_has_permission = None


def restricted_user_has_permission(user, obj, permission):
    if user and not user.is_anonymous and user.is_superuser:
        return _original_user_has_permission(user, obj, permission)

    allowed = _original_user_has_permission(user, obj, permission)
    if not allowed:
        return False

    from dojo_permission.policy import policy_denies_permission

    normalized_permission = permission if isinstance(permission, Permissions) else Permissions(permission)
    return not policy_denies_permission(user, obj, normalized_permission)


def install_authorization_overlay() -> None:
    global _installed, _original_user_has_permission
    if _installed:
        return

    from dojo.authorization import authorization

    _original_user_has_permission = authorization.user_has_permission
    authorization.user_has_permission = restricted_user_has_permission

    from dojo.api_v2 import permissions as api_permissions
    from dojo.templatetags import authorization_tags

    api_permissions.user_has_permission = restricted_user_has_permission
    authorization_tags.user_has_permission = restricted_user_has_permission
    _installed = True
