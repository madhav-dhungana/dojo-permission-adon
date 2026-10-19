"""Single-group fixtures with independent native product grants."""

from datetime import date

from django.contrib.auth import get_user_model
from django.utils import timezone

from dojo.authorization.roles_permissions import Roles
from dojo.models import (
    Dojo_Group, Dojo_Group_Member, Engagement, Finding, Product, Product_Group,
    Product_Member, Product_Type, Role, SLA_Configuration, Test, Test_Type,
)
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.provisioning import ensure_default_managed_group

User = get_user_model()


class SingleGroupFixture:
    def build_fixture(self):
        self.reader = Role.objects.get(pk=Roles.Reader)
        self.writer = Role.objects.get(pk=Roles.Writer)
        self.maintainer = Role.objects.get(pk=Roles.Maintainer)
        self.owner = Role.objects.get(pk=Roles.Owner)
        self.policy = ensure_default_managed_group()
        self.a = self.product("P1")
        self.b = self.product("P2")
        self.c = self.product("P3")
        self.user = self.account("member-a", self.a)
        self.foreign = self.account("private-member-b", self.b)
        self.third = self.account("private-member-c", self.c)
        self.membership = Dojo_Group_Member.objects.get(group=self.policy.group, user=self.user)
        self.engagement, self.test, self.finding = self.finding_scope(self.a, self.user)
        self.other_engagement, self.other_test, self.other_finding = self.finding_scope(self.b, self.foreign)
        self.third_engagement, self.third_test, self.third_finding = self.finding_scope(self.c, self.third)
        self.client.force_login(self.user)

    def product(self, name):
        return Product.objects.create(
            name=name, description=f"Test product {name}",
            prod_type=Product_Type.objects.create(name=f"Type {name}"),
            sla_configuration=SLA_Configuration.objects.first(),
        )

    def account(self, username, product=None, *, group_role=None, product_role=None):
        user = User.objects.create_user(username=username, email=f"{username}@example.test")
        Dojo_Group_Member.objects.create(group=self.policy.group, user=user, role=group_role or self.reader)
        if product is not None:
            Product_Member.objects.create(product=product, user=user, role=product_role or self.writer)
        return user

    def finding_scope(self, product, reporter):
        engagement = Engagement.objects.create(
            name=f"Existing engagement {product.name}", product=product,
            target_start=date.today(), target_end=date.today(),
        )
        test = Test.objects.create(
            engagement=engagement, test_type=Test_Type.objects.first(),
            target_start=timezone.now(), target_end=timezone.now(),
        )
        finding = Finding.objects.create(
            title=f"Existing finding {product.name}", test=test, severity="Low",
            reporter=reporter, verified=True,
        )
        return engagement, test, finding

    def legacy_policy(self, product, *, name=None, description=None, bound=True, created_by=None):
        group = Dojo_Group.objects.create(
            name=name or f"Dev Team group - Product {product.pk}",
            description=description if description is not None else f"Managed by dojo-permission for exactly one product: {product.pk}.",
        )
        policy = RestrictedGroupPolicy.objects.create(
            group=group, product=product if bound else None, created_by=created_by,
        )
        Product_Group.objects.create(product=product, group=group, role=self.writer)
        return policy
