from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from dojo_permission.forms import RestrictedDojoGroupForm
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.services import requester_policy_membership

_installed = False
_original_view_group_get = None


def _add_group_form(_view, request):
    args = [request.POST] if request.method == "POST" else []
    return RestrictedDojoGroupForm(*args, policy_actor=request.user)


def _edit_group_form(_view, request, group):
    args = [request.POST] if request.method == "POST" else []
    return RestrictedDojoGroupForm(
        *args,
        instance=group,
        policy_actor=request.user,
    )


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
    """Add the checkbox and route restricted members to their limited page."""
    global _installed, _original_view_group_get
    if _installed:
        return

    from dojo.group import views as group_views

    _original_view_group_get = group_views.ViewGroup.get
    group_views.ViewGroup.get = _view_restricted_group
    group_views.AddGroup.get_group_form = _add_group_form
    group_views.EditGroup.get_group_form = _edit_group_form
    _installed = True
