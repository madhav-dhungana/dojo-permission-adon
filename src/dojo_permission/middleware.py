from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from dojo.authorization.roles_permissions import Permissions
from dojo.models import Finding

from dojo_permission.policy import policy_denies_permission


class RestrictedActionMiddleware:
    """Defense in depth for actions that reuse broader update permissions."""

    def __init__(self, get_response):
        from dojo_permission.group_integration import install_group_ui_overlay

        install_group_ui_overlay()
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
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
