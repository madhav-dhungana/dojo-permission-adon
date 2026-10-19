from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from dojo.authorization.roles_permissions import Permissions
from dojo.models import Dojo_Group, Engagement, Finding, Test

from dojo_permission.policy import (
    is_restricted_user,
    is_shared_restricted_user,
    policy_denies_permission,
    shared_policy_denies_object_scope,
)


FINDING_TEMPLATE_VIEWS = frozenset({
    "mktemplate",
    "find_template_to_apply",
    "choose_finding_template_options",
    "apply_template_to_finding",
    "templates",
    "export_templates_to_json",
    "add_template",
    "edit_template",
    "delete_template",
})


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
        view_class = getattr(view_func, "cls", None)
        view_class_name = getattr(view_class, "__name__", None)
        view_class_module = getattr(view_class, "__module__", None)
        if module in {
            "dojo.metrics.views",
            "dojo.product_type.views",
        } and is_shared_restricted_user(request.user):
            raise PermissionDenied
        if (
            module == "dojo.finding.views"
            and name in FINDING_TEMPLATE_VIEWS
            and is_shared_restricted_user(request.user)
        ):
            raise PermissionDenied
        if (
            view_class_module == "dojo.api_v2.views"
            and view_class_name == "FindingTemplatesViewSet"
            and is_shared_restricted_user(request.user)
        ):
            raise PermissionDenied
        if (
            view_class_module == "dojo.api_v2.views"
            and view_class_name == "ProductTypeViewSet"
            and request.method == "POST"
            and is_shared_restricted_user(request.user)
        ):
            raise PermissionDenied
        if (
            module == "dojo.url.ui.views"
            and name == "add_endpoint_to_finding"
            and is_shared_restricted_user(request.user)
        ):
            finding = get_object_or_404(
                Finding.objects.select_related("test__engagement__product__prod_type"),
                pk=view_kwargs.get("finding_id"),
            )
            from dojo.authorization.authorization import user_has_permission

            if (
                shared_policy_denies_object_scope(request.user, finding)
                or not user_has_permission(
                    request.user,
                    finding.test.engagement.product,
                    Permissions.Location_Add,
                )
            ):
                raise PermissionDenied
        if (
            module == "dojo.engagement.views"
            and name == "add_risk_acceptance"
            and view_kwargs.get("fid") is not None
            and is_shared_restricted_user(request.user)
        ):
            finding = get_object_or_404(
                Finding.objects.select_related("test__engagement__product__prod_type"),
                pk=view_kwargs["fid"],
            )
            if (
                str(finding.test.engagement_id) != str(view_kwargs.get("eid"))
                or shared_policy_denies_object_scope(request.user, finding)
            ):
                raise PermissionDenied
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
