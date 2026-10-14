from django.conf import settings
from django.db import models


class RestrictedGroupPolicy(models.Model):
    """Marks a DefectDojo group as a restricted Writer delegation group."""

    group = models.OneToOneField(
        "dojo.Dojo_Group",
        on_delete=models.PROTECT,
        related_name="dojo_permission_policy",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_dojo_permission_policies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("group__name",)
        verbose_name = "restricted group policy"
        verbose_name_plural = "restricted group policies"

    def __str__(self) -> str:
        return self.group.name
