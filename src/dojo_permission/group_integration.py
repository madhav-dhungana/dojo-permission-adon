from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.services import requester_policy_membership

_installed = False
_original_view_group_get = None


def _view_restricted_group(view, request, group_id):
    """Send eligible restricted members to their deliberately limited UI."""
    if request.user.is_authenticated and not request.user.is_superuser:
        policy = RestrictedGroupPolicy.objects.filter(
            group_id=group_id,
            is_active=True,
        ).first()
        if policy is not None:
            try:
                requester_policy_membership(request.user, policy)
            except PermissionDenied:
                pass
            else:
                return redirect(
                    "dojo_permission:manage_policy_members",
                    policy_id=policy.id,
                )

    return _original_view_group_get(view, request, group_id)


def install_group_ui_overlay() -> None:
    """Route eligible members to the narrow account-invitation page."""
    global _installed, _original_view_group_get
    if _installed:
        return

    from dojo.group import views as group_views

    _original_view_group_get = group_views.ViewGroup.get
    group_views.ViewGroup.get = _view_restricted_group
    _installed = True
