from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from dojo_permission.models import RestrictedGroupPolicy


class RestrictedGroupPolicyAdmin(admin.ModelAdmin):
    list_display = ("group", "is_active", "created_by", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("group__name",)


def register_admin() -> None:
    try:
        admin.site.register(RestrictedGroupPolicy, RestrictedGroupPolicyAdmin)
    except AlreadyRegistered:
        pass
