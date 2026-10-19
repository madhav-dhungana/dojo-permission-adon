from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from dojo.authorization.roles_permissions import Permissions
from dojo.models import Dojo_Group, Engagement, Finding, Test

from dojo_permission.policy import is_restricted_user, policy_denies_permission


class RestrictedActionMiddleware:
    """Defense in depth for actions that reuse broader update permissions."""

    def __init__(self, get_response):
        from dojo_permission.group_integration import install_group_ui_overlay
        from dojo_permission.privacy import install_privacy_overlay

        install_group_ui_overlay()
        install_privacy_overlay()
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        module = getattr(view_func, "__module__", None)
        name = getattr(view_func, "__name__", None)
        if module == "dojo.user.views" and name in {
            "user", "add_user", "view_user", "edit_user", "delete_user", "add_group_member",
        } and is_restricted_user(request.user):
            raise PermissionDenied
        match = getattr(request, "resolver_match", None)
        if match is not None and match.namespace == "admin" and is_restricted_user(request.user):
            raise PermissionDenied
        if module == "dojo.group.views" and name == "edit_permissions":
            group = get_object_or_404(Dojo_Group, pk=view_kwargs.get("gid"))
            if policy_denies_permission(request.user, group, Permissions.Group_Edit):
                raise PermissionDenied

        is_core_copy_finding = (
            getattr(view_func, "__name__", None) == "copy_finding"
            and getattr(view_func, "__module__", None) == "dojo.finding.views"
        )
        if is_core_copy_finding:
            finding = get_object_or_404(
                Finding.objects.select_related("test__engagement__product__prod_type"),
                id=view_kwargs.get("fid"),
            )
            if policy_denies_permission(request.user, finding, Permissions.Finding_Add):
                raise PermissionDenied

        copies = {
            ("dojo.engagement.views", "copy_engagement"): (Engagement, "eid", Permissions.Engagement_Add),
            ("dojo.test.views", "copy_test"): (Test, "tid", Permissions.Test_Add),
        }
        if (module, name) in copies:
            model, argument, permission = copies[(module, name)]
            obj = get_object_or_404(model, pk=view_kwargs.get(argument))
            if policy_denies_permission(request.user, obj, permission):
                raise PermissionDenied

        if request.method != "POST" or "delete_bulk_findings" not in request.POST:
            return None

        finding_ids = request.POST.getlist("finding_to_update")
        findings = Finding.objects.filter(id__in=finding_ids).select_related(
            "test__engagement__product__prod_type",
        )
        if any(
            policy_denies_permission(request.user, finding, Permissions.Finding_Delete)
            for finding in findings
        ):
            raise PermissionDenied
        return None
