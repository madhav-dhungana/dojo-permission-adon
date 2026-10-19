from django.conf import settings
from django.db import models


class RestrictedGroupPolicy(models.Model):
    """Marks a DefectDojo group as a restricted Writer delegation group."""

    group = models.OneToOneField(
        "dojo.Dojo_Group",
        on_delete=models.CASCADE,
        related_name="dojo_permission_policy",
    )
    product = models.ForeignKey(
        "dojo.Product",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="dojo_permission_policies",
    )
    is_default = models.BooleanField(default=False, db_default=False)
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
        constraints = (
            models.UniqueConstraint(
                fields=("product",),
                condition=models.Q(product__isnull=False),
                name="dojo_permission_unique_product_policy",
            ),
            models.UniqueConstraint(
                fields=("is_default",),
                condition=models.Q(is_default=True),
                name="dojo_permission_unique_default_policy",
            ),
        )
        ordering = ("group__name",)
        verbose_name = "restricted group policy"
        verbose_name_plural = "restricted group policies"

    def __str__(self) -> str:
        return self.group.name
