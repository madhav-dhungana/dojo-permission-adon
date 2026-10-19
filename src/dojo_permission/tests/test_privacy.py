"""User selectors and native API/admin paths must respect product boundaries."""

from datetime import date

from crum import impersonate
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from dojo.api_v2.serializers import DojoGroupSerializer, FindingSerializer
from dojo.filters import FindingFilter
from dojo.forms import FindingForm
from dojo.models import (
    Dojo_Group,
    Dojo_Group_Member,
    Engagement,
    Finding,
    Global_Role,
    Product,
    Product_Type,
    Product_Type_Member,
    Role,
    SLA_Configuration,
    Test,
    Test_Type,
)
from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions, Roles
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.privacy import install_privacy_overlay, visible_user_ids

User = get_user_model()


class ProductPrivacyTests(TestCase):
    def setUp(self):
        install_privacy_overlay()
        self.role = Role.objects.get(pk=Roles.Reader)
        self.a = Product.objects.create(
            name="Privacy product A", description="A",
            prod_type=Product_Type.objects.create(name="Privacy type A"),
            sla_configuration=SLA_Configuration.objects.first(),
        )
        self.b = Product.objects.create(
            name="Privacy product B", description="B",
            prod_type=Product_Type.objects.create(name="Privacy type B"),
            sla_configuration=SLA_Configuration.objects.first(),
        )
        self.policy = RestrictedGroupPolicy.objects.get(product=self.a)
        self.other_policy = RestrictedGroupPolicy.objects.get(product=self.b)
        self.actor = User.objects.create_user(username="privacy-actor", email="actor@example.test")
        self.peer_a = User.objects.create_user(username="same-product-a", email="same-a@example.test")
        self.peer_b = User.objects.create_user(username="private-product-b", email="private-b@example.test")
        for user, policy in ((self.actor, self.policy), (self.actor, self.other_policy), (self.peer_a, self.policy), (self.peer_b, self.other_policy)):
            Dojo_Group_Member.objects.create(group=policy.group, user=user, role=self.role)
        self.engagement = Engagement.objects.create(
            name="Privacy engagement A", product=self.a,
            target_start=date.today(), target_end=date.today(),
        )
        self.test = Test.objects.create(
            engagement=self.engagement, test_type=Test_Type.objects.first(),
            target_start=timezone.now(), target_end=timezone.now(),
        )
        self.finding = Finding.objects.create(
            title="Privacy finding A", test=self.test, severity="Low", reporter=self.actor,
        )
        self.client.force_login(self.actor)

    def request(self):
        request = RequestFactory().patch(f"/api/v2/findings/{self.finding.pk}/")
        request.user = self.actor
        return request

    def only_product_a(self):
        Dojo_Group_Member.objects.filter(group=self.other_policy.group, user=self.actor).delete()

    def test_explicit_product_visibility_is_narrower_than_all_authorized_products(self):
        self.assertIn(self.peer_b.pk, visible_user_ids(self.actor))
        self.assertIn(self.peer_a.pk, visible_user_ids(self.actor, self.a))
        self.assertNotIn(self.peer_b.pk, visible_user_ids(self.actor, self.a))

    def test_scoped_group_owner_exemption_wins_over_the_default_marker(self):
        marker = Dojo_Group.objects.get(name="Restricted Finding Editors")
        Dojo_Group_Member.objects.create(group=marker, user=self.actor, role=self.role)
        Dojo_Group_Member.objects.filter(group=self.policy.group, user=self.actor).update(role_id=Roles.Owner)
        self.assertTrue(user_has_permission(self.actor, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.actor, self.b, Permissions.Engagement_Edit))

    def test_native_finding_form_uses_the_finding_product(self):
        with impersonate(self.actor):
            form = FindingForm(instance=self.finding, can_edit_mitigated_data=True)
        choices = set(form.fields["mitigated_by"].queryset.values_list("pk", flat=True))
        self.assertIn(self.peer_a.pk, choices)
        self.assertNotIn(self.peer_b.pk, choices)

    def test_native_finding_filter_scopes_reporters_to_selected_product(self):
        with impersonate(self.actor):
            filtered = FindingFilter({}, queryset=Finding.objects.all(), pid=self.a.pk, user=self.actor)
        for field in ("reporter", "reviewers"):
            choices = set(filtered.form.fields[field].queryset.values_list("pk", flat=True))
            self.assertIn(self.peer_a.pk, choices)
            self.assertNotIn(self.peer_b.pk, choices)

    def test_native_finding_filter_scopes_engagement_and_test_contexts(self):
        for context in ({"eid": self.engagement.pk}, {"tid": self.test.pk}):
            with self.subTest(context=context), impersonate(self.actor):
                filtered = FindingFilter({}, queryset=Finding.objects.all(), user=self.actor, **context)
                self.assertNotIn(self.peer_b.pk, filtered.form.fields["reporter"].queryset.values_list("pk", flat=True))

    def test_native_finding_api_serializer_limits_user_fields_to_its_product(self):
        serializer = FindingSerializer(instance=self.finding, context={"request": self.request()})
        for field in ("reporter", "mitigated_by"):
            self.assertIn(self.peer_a.pk, serializer.fields[field].queryset.values_list("pk", flat=True))
            self.assertNotIn(self.peer_b.pk, serializer.fields[field].queryset.values_list("pk", flat=True))

    def test_forged_finding_user_assignment_is_rejected(self):
        serializer = FindingSerializer(
            instance=self.finding, data={"mitigated_by": self.peer_b.pk}, partial=True,
            context={"request": self.request()},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("mitigated_by", serializer.errors)

    def test_api_prefetch_does_not_expand_another_products_user_identity(self):
        self.finding.reporter = self.peer_b
        self.finding.save(update_fields=("reporter",))
        response = self.client.get(f"/api/v2/findings/{self.finding.pk}/", {"prefetch": "reporter"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.peer_b.username)
        self.assertNotContains(response, self.peer_b.email)
        prefetched = response.json()["prefetch"].get("reporter", {})
        for value in prefetched.values():
            self.assertEqual(set(value), {"id"})

    def test_native_tests_viewset_prefetch_uses_the_test_product(self):
        self.test.lead = self.peer_b
        self.test.save(update_fields=("lead",))
        response = self.client.get(f"/api/v2/tests/{self.test.pk}/", {"prefetch": "lead"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.peer_b.username)
        self.assertNotContains(response, self.peer_b.email)
        for value in response.json()["prefetch"].get("lead", {}).values():
            self.assertEqual(set(value), {"id"})

    def test_shared_marker_serializer_does_not_include_foreign_user_ids(self):
        self.only_product_a()
        marker = Dojo_Group.objects.get(name="Restricted Finding Editors")
        for user in (self.actor, self.peer_b):
            Dojo_Group_Member.objects.create(group=marker, user=user, role=self.role)
        request = self.request()
        result = DojoGroupSerializer(marker, context={"request": request}).data
        self.assertIn(self.actor.pk, result["users"])
        self.assertNotIn(self.peer_b.pk, result["users"])

    def test_shared_marker_member_api_does_not_list_or_retrieve_foreign_enrollment(self):
        self.only_product_a()
        marker = Dojo_Group.objects.get(name="Restricted Finding Editors")
        Dojo_Group_Member.objects.create(group=marker, user=self.actor, role=self.role)
        foreign = Dojo_Group_Member.objects.create(group=marker, user=self.peer_b, role=self.role)
        response = self.client.get("/api/v2/dojo_group_members/", {"group_id": marker.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.peer_b.pk, [item["user"] for item in response.json()["results"]])
        self.assertEqual(self.client.get(f"/api/v2/dojo_group_members/{foreign.pk}/").status_code, 404)

    def test_staff_member_cannot_bypass_scope_controls_in_native_admin(self):
        self.actor.is_staff = True
        self.actor.save(update_fields=("is_staff",))
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="dojo", codename__in=("change_dojo_group", "change_finding")))
        url = reverse("admin:dojo_dojo_group_change", args=(self.policy.group_id,))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"name": "Unauthorized rename"}).status_code, 403)
        self.policy.group.refresh_from_db()
        self.assertNotEqual(self.policy.group.name, "Unauthorized rename")
        finding_url = reverse("admin:dojo_finding_change", args=(self.finding.pk,))
        self.assertEqual(self.client.get(finding_url).status_code, 403)

    def test_auto_created_import_scope_cannot_bypass_a_restricted_global_group(self):
        from dojo.api_v2 import permissions

        legacy = Dojo_Group.objects.create(name="Legacy global restricted import")
        RestrictedGroupPolicy.objects.create(group=legacy)
        Global_Role.objects.create(group=legacy, role_id=Roles.Maintainer)
        Dojo_Group_Member.objects.create(group=legacy, user=self.actor, role=self.role)
        arguments = dict(user=self.actor, product=None, product_name="Not yet created",
                         engagement=None, engagement_name="New engagement", product_type=self.b.prod_type,
                         product_type_name=self.b.prod_type.name, error_message="An import target is required")
        self.assertFalse(permissions.check_auto_create_permission(**arguments))
        Product_Type_Member.objects.create(product_type=self.b.prod_type, user=self.actor, role_id=Roles.Owner)
        self.assertTrue(permissions.check_auto_create_permission(**arguments))

    def test_superuser_keeps_native_user_selector_scope(self):
        admin = User.objects.create_superuser(username="privacy-admin", email="privacy-admin@example.test")
        request = self.request()
        request.user = admin
        serializer = FindingSerializer(instance=self.finding, context={"request": request})
        self.assertIn(self.peer_b.pk, serializer.fields["mitigated_by"].queryset.values_list("pk", flat=True))

    def test_unrestricted_user_keeps_native_user_selector_scope(self):
        user = User.objects.create_user(username="unrestricted-privacy-user", email="unrestricted@example.test")
        request = self.request()
        request.user = user
        serializer = FindingSerializer(instance=self.finding, context={"request": request})
        self.assertIn(self.peer_b.pk, serializer.fields["mitigated_by"].queryset.values_list("pk", flat=True))
