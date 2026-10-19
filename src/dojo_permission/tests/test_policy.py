"""Managed policy, multi-product isolation, and real native UI/API regressions."""
from datetime import date
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions, Roles
from dojo.models import (
    Dojo_Group, Dojo_Group_Member, Engagement, Finding, Global_Role, Product,
    Product_Group, Product_Member, Product_Type, Product_Type_Group, Role,
    SLA_Configuration, System_Settings, Test, Test_Type,
)
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.policy import policy_denies_permission
from dojo_permission.services import add_existing_user_to_policy, create_invited_user, send_password_invitation, requester_policy_membership, InvitationEmailError

User = get_user_model()
DENIED = (
    Permissions.Engagement_Add, Permissions.Engagement_Edit, Permissions.Engagement_Delete,
    Permissions.Test_Add, Permissions.Import_Scan_Result, Permissions.Finding_Add,
    Permissions.Finding_Delete, Permissions.Product_Delete, Permissions.Product_Group_Add,
    Permissions.Product_Group_Edit, Permissions.Product_Group_Delete,
)


class ManagedPolicyTests(TestCase):
    def setUp(self):
        self.reader = Role.objects.get(pk=Roles.Reader)
        self.writer = Role.objects.get(pk=Roles.Writer)
        self.maintainer = Role.objects.get(pk=Roles.Maintainer)
        self.owner = Role.objects.get(pk=Roles.Owner)
        self.a = Product.objects.create(name="Product A", description="A", prod_type=Product_Type.objects.create(name="Type A"), sla_configuration=SLA_Configuration.objects.first())
        self.b = Product.objects.create(name="Product B", description="B", prod_type=Product_Type.objects.create(name="Type B"), sla_configuration=SLA_Configuration.objects.first())
        self.policy = RestrictedGroupPolicy.objects.get(product=self.a)
        self.other_policy = RestrictedGroupPolicy.objects.get(product=self.b)
        self.user = User.objects.create_user(username="member-a", email="member-a@example.test")
        self.foreign = User.objects.create_user(username="private-member-b", email="private-b@example.test")
        self.membership = Dojo_Group_Member.objects.create(group=self.policy.group, user=self.user, role=self.reader)
        Dojo_Group_Member.objects.create(group=self.other_policy.group, user=self.foreign, role=self.reader)
        self.engagement = Engagement.objects.create(name="Existing engagement", product=self.a, target_start=date.today(), target_end=date.today())
        self.test = Test.objects.create(engagement=self.engagement, test_type=Test_Type.objects.first(), target_start=timezone.now(), target_end=timezone.now())
        self.finding = Finding.objects.create(title="Existing finding", test=self.test, severity="Low", reporter=self.user, verified=True)
        self.client.force_login(self.user)

    def invite(self, **kwargs):
        values = dict(requester=self.user, policy=self.policy, username="invited-new", email="invited-new@example.test")
        values.update(kwargs)
        return create_invited_user(**values)

    def test_product_signal_provisions_one_scoped_group(self):
        self.assertTrue(self.policy.is_active)
        assignment = Product_Group.objects.get(group=self.policy.group)
        self.assertEqual((assignment.product_id, assignment.role_id), (self.a.pk, Roles.Writer))
        self.assertNotEqual(self.policy.group_id, self.other_policy.group_id)
        self.assertFalse(Global_Role.objects.filter(group=self.policy.group, role__isnull=False).exists())
        self.assertFalse(self.policy.group.auth_group.permissions.exists())

    def test_post_migrate_provisioning_is_idempotent(self):
        before = (RestrictedGroupPolicy.objects.count(), Dojo_Group.objects.count(), Product_Group.objects.count())
        call_command("migrate", "dojo_permission", interactive=False, verbosity=0)
        self.assertEqual(before, (RestrictedGroupPolicy.objects.count(), Dojo_Group.objects.count(), Product_Group.objects.count()))

    def test_admin_rename_does_not_duplicate_owned_groups(self):
        from dojo_permission.provisioning import ensure_default_managed_group, ensure_product_policy
        marker = ensure_default_managed_group()
        marker.group.name = "Administrator renamed marker"
        marker.group.description = "Administrator description"
        marker.group.save()
        self.policy.group.name = "Administrator renamed product group"
        self.policy.group.save()
        self.assertEqual(ensure_default_managed_group().pk, marker.pk)
        self.assertEqual(ensure_product_policy(self.a).pk, self.policy.pk)

    def test_owned_name_collision_does_not_adopt_unrelated_group(self):
        from dojo_permission.provisioning import ensure_product_policy, managed_product_group_name, ManagedGroupCollision
        self.policy.group.delete()
        collision = Dojo_Group.objects.create(name=managed_product_group_name(self.a), description="An unrelated pre-existing group")
        with self.assertRaises(ManagedGroupCollision):
            ensure_product_policy(self.a)
        self.assertFalse(RestrictedGroupPolicy.objects.filter(group=collision).exists())

    def test_provisioning_collision_does_not_leave_a_half_created_product(self):
        from dojo_permission.provisioning import ManagedGroupCollision
        Dojo_Group.objects.create(name="Reserved collision", description="Unrelated group")
        with patch("dojo_permission.provisioning.managed_product_group_name", return_value="Reserved collision"):
            with self.assertRaises(ManagedGroupCollision):
                Product.objects.create(name="Must not remain after collision", description="Test", prod_type=self.a.prod_type)
        self.assertFalse(Product.objects.filter(name="Must not remain after collision").exists())

    def test_new_product_does_not_auto_make_requester_policy_owner(self):
        from crum import impersonate
        with impersonate(self.user):
            product = Product.objects.create(name="Created under request", description="Test", prod_type=self.a.prod_type)
        policy = RestrictedGroupPolicy.objects.get(product=product)
        self.assertFalse(Dojo_Group_Member.objects.filter(group=policy.group, user=self.user, role=self.owner).exists())

    def test_product_fk_remains_compatible_with_old_image_deletion(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL deployment constraint")
        with connection.cursor() as cursor:
            cursor.execute("SELECT confdeltype FROM pg_constraint WHERE conrelid = %s::regclass AND confrelid = %s::regclass", [RestrictedGroupPolicy._meta.db_table, Product._meta.db_table])
            actions = [row[0] for row in cursor.fetchall()]
        self.assertEqual(actions, ["c"])

    def test_default_marker_is_installed_without_access_grants(self):
        group = Dojo_Group.objects.get(name="Restricted Finding Editors")
        self.assertTrue(RestrictedGroupPolicy.objects.get(group=group).is_active)
        self.assertFalse(Product_Group.objects.filter(group=group).exists())
        self.assertFalse(Product_Type_Group.objects.filter(group=group).exists())
        target = User.objects.create_user(username="marker-member", email="marker@example.test")
        Dojo_Group_Member.objects.create(group=group, user=target, role=self.reader)
        self.assertFalse(user_has_permission(target, self.a, Permissions.Product_View))
        Product_Member.objects.create(product=self.a, user=target, role=self.writer)
        self.assertTrue(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.a, Permissions.Engagement_Edit))

    def test_group_reader_writer_and_maintainer_all_receive_restrictions(self):
        for role in (self.reader, self.writer, self.maintainer):
            self.membership.role = role
            self.membership.save(update_fields=("role",))
            for permission in DENIED:
                with self.subTest(role=role, permission=permission):
                    self.assertFalse(user_has_permission(self.user, self.a, permission))
            self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))

    def test_assignment_role_does_not_silently_disable_restrictions(self):
        for role in (self.reader, self.writer, self.maintainer, self.owner):
            Product_Group.objects.filter(group=self.policy.group).update(role=role)
            self.assertTrue(policy_denies_permission(self.user, self.a, Permissions.Engagement_Edit))

    def test_group_owner_preserves_native_permissions(self):
        self.membership.role = self.owner
        self.membership.save(update_fields=("role",))
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))

    def test_duplicate_owner_membership_keeps_native_owner_flow(self):
        Dojo_Group_Member.objects.create(group=self.policy.group, user=self.user, role=self.owner)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        with self.assertRaises(PermissionDenied):
            requester_policy_membership(self.user, self.policy)

    def test_independent_maintainer_is_not_silently_downgraded(self):
        Product_Member.objects.create(product=self.a, user=self.user, role=self.maintainer)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))

    def test_legacy_checked_group_preserves_active_policy_on_upgrade(self):
        group = Dojo_Group.objects.create(name="Legacy checked group")
        policy = RestrictedGroupPolicy.objects.create(group=group)
        target = User.objects.create_user(username="legacy-member", email="legacy@example.test")
        Dojo_Group_Member.objects.create(group=group, user=target, role=self.reader)
        Product_Group.objects.create(product=self.a, group=group, role=self.maintainer)
        self.assertFalse(user_has_permission(target, self.a, Permissions.Finding_Delete))
        self.assertTrue(policy.is_active)

    def test_legacy_policy_group_global_writer_is_restricted(self):
        group = Dojo_Group.objects.create(name="Legacy global restricted group")
        RestrictedGroupPolicy.objects.create(group=group)
        Dojo_Group_Member.objects.create(group=group, user=self.user, role=self.reader)
        Global_Role.objects.create(group=group, role=self.writer)
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Engagement_Edit))

    def test_inactive_policy_group_can_be_deleted(self):
        group = Dojo_Group.objects.create(name="Inactive legacy group")
        policy = RestrictedGroupPolicy.objects.create(group=group, is_active=False)
        policy_id = policy.pk
        group.delete()
        self.assertFalse(RestrictedGroupPolicy.objects.filter(pk=policy_id).exists())

    def test_invitation_page_does_not_list_existing_accounts(self):
        response = self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<button class="btn btn-success" type="submit">')
        for text in ("Add an existing account", "existing-user", self.foreign.username, self.foreign.email):
            self.assertNotContains(response, text)

    def test_native_user_menu_discovers_restricted_account_creation(self):
        response = self.client.get(reverse("product"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dojo-permission-create-account"')
        self.assertContains(response, reverse("dojo_permission:invitation_entry"))

    def test_single_invitation_scope_redirects_to_its_form(self):
        response = self.client.get(reverse("dojo_permission:invitation_entry"))
        self.assertRedirects(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)),
            fetch_redirect_response=False,
        )

    def test_multiple_invitation_scopes_have_a_private_chooser(self):
        Dojo_Group_Member.objects.create(
            group=self.other_policy.group,
            user=self.user,
            role=self.writer,
        )
        response = self.client.get(reverse("dojo_permission:invitation_entry"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)),
        )
        self.assertContains(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.other_policy.pk,)),
        )
        self.assertNotContains(response, self.foreign.email)

    def test_owner_does_not_get_restricted_invitation_menu_entry(self):
        self.membership.role = self.owner
        self.membership.save(update_fields=("role",))
        response = self.client.get(reverse("product"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="dojo-permission-create-account"')
        self.assertEqual(
            self.client.get(reverse("dojo_permission:invitation_entry")).status_code,
            403,
        )

    def test_foreign_policy_page_and_service_are_denied(self):
        response = self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.other_policy.pk,)))
        self.assertIn(response.status_code, (403, 404))
        with self.assertRaises(PermissionDenied):
            self.invite(policy=self.other_policy)

    def test_existing_account_assignment_is_admin_only(self):
        with self.assertRaises(PermissionDenied):
            add_existing_user_to_policy(requester=self.user, target_user=self.foreign, policy=self.policy)
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {"action": "add_existing", "existing-user": self.foreign.pk})
        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(Dojo_Group_Member.objects.filter(group=self.policy.group, user=self.foreign).exists())

    def test_new_user_inherits_only_selected_product_policy_and_group_role(self):
        target = self.invite()
        self.assertFalse(target.has_usable_password())
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)
        members = Dojo_Group_Member.objects.filter(user=target)
        self.assertEqual(list(members.values_list("group_id", "role_id")), [(self.policy.group_id, self.reader.pk)])
        self.assertTrue(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))
        self.assertFalse(user_has_permission(target, self.a, Permissions.Engagement_Edit))

    def test_bound_policy_cannot_be_submitted_for_another_product(self):
        with self.assertRaises(PermissionDenied):
            self.invite(product=self.b)
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_marker_invitation_grants_only_one_selected_product(self):
        marker = RestrictedGroupPolicy.objects.get(is_default=True)
        Dojo_Group_Member.objects.create(group=marker.group, user=self.user, role=self.reader)
        Product_Member.objects.create(product=self.b, user=self.user, role=self.writer)
        target = self.invite(policy=marker, product=self.a)
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", flat=True)), [self.policy.group_id])
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_marker_invitation_cannot_grant_a_foreign_product(self):
        marker = RestrictedGroupPolicy.objects.get(is_default=True)
        Dojo_Group_Member.objects.create(group=marker.group, user=self.user, role=self.reader)
        with self.assertRaises(PermissionDenied):
            self.invite(policy=marker, product=self.b)

    def test_marker_read_only_access_cannot_be_delegated_as_writer(self):
        marker = RestrictedGroupPolicy.objects.get(is_default=True)
        reader = User.objects.create_user(username="marker-reader", email="marker-reader@example.test")
        Dojo_Group_Member.objects.create(group=marker.group, user=reader, role=self.reader)
        Product_Member.objects.create(product=self.a, user=reader, role=self.reader)
        self.assertTrue(user_has_permission(reader, self.a, Permissions.Product_View))
        self.assertFalse(user_has_permission(reader, self.a, Permissions.Finding_Edit))
        with self.assertRaises(PermissionDenied):
            self.invite(requester=reader, policy=marker, product=self.a)
        self.assertFalse(User.objects.filter(username="invited-new").exists())
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse("dojo_permission:invitation_entry")).status_code, 403)

    def test_marker_product_choices_exclude_read_only_products(self):
        marker = RestrictedGroupPolicy.objects.get(is_default=True)
        Dojo_Group_Member.objects.create(group=marker.group, user=self.user, role=self.reader)
        Product_Member.objects.create(product=self.b, user=self.user, role=self.reader)
        response = self.client.get(reverse("dojo_permission:manage_policy_members", args=(marker.pk,)))
        self.assertEqual(response.status_code, 200)
        choices = response.context["invitation_form"].fields["product"].queryset
        self.assertEqual(list(choices.values_list("pk", flat=True)), [self.a.pk])
        with self.assertRaises(PermissionDenied):
            self.invite(policy=marker, product=self.b)

    def test_marker_native_writer_can_invite_with_unchanged_group_role(self):
        marker = RestrictedGroupPolicy.objects.get(is_default=True)
        writer = User.objects.create_user(username="marker-writer", email="marker-writer@example.test")
        Dojo_Group_Member.objects.create(group=marker.group, user=writer, role=self.reader)
        Product_Member.objects.create(product=self.a, user=writer, role=self.writer)
        target = self.invite(requester=writer, policy=marker, product=self.a)
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", "role_id")), [(self.policy.group_id, self.reader.pk)])
        self.assertTrue(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_navigation_tag_tolerates_missing_request_context(self):
        from dojo_permission.templatetags.dojo_permission_tags import dojo_permission_can_invite
        self.assertFalse(dojo_permission_can_invite(None))
        self.assertFalse(dojo_permission_can_invite(""))

    def test_sso_creation_page_assigns_only_scoped_membership(self):
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
            "action": "invite_new", "invite-username": "sso-page-new",
            "invite-email": "sso-page@example.test", "invite-authentication_method": "sso",
        })
        self.assertEqual(response.status_code, 302)
        target = User.objects.get(username="sso-page-new")
        self.assertFalse(target.has_usable_password())
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_email_failure_does_not_claim_success_or_lose_created_membership(self):
        with patch("dojo_permission.views.send_password_invitation", side_effect=InvitationEmailError):
            response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
                "action": "invite_new", "invite-username": "email-page-new",
                "invite-email": "email-page@example.test", "invite-authentication_method": "email",
            }, follow=True)
        self.assertContains(response, "setup email could not be sent")
        self.assertNotContains(response, "password setup email was sent")
        target = User.objects.get(username="email-page-new")
        self.assertTrue(Dojo_Group_Member.objects.filter(group=self.policy.group, user=target).exists())

    def test_default_group_cannot_escape_new_user_scope(self):
        settings = System_Settings.objects.get()
        settings.default_group = self.other_policy.group
        settings.default_group_role = self.maintainer
        settings.default_group_email_pattern = ""
        settings.save()
        target = self.invite()
        self.assertFalse(Dojo_Group_Member.objects.filter(group=self.other_policy.group, user=target).exists())
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))
        self.assertEqual(Dojo_Group_Member.objects.filter(user=target).count(), 1)

    def test_duplicate_account_is_not_modified_or_identified(self):
        before = list(Dojo_Group_Member.objects.filter(user=self.foreign).values_list("group_id", "role_id"))
        with self.assertRaises(ValidationError) as exc:
            self.invite(username=self.foreign.username, email=self.foreign.email)
        self.assertNotIn(self.foreign.username, str(exc.exception))
        self.assertNotIn(self.foreign.email, str(exc.exception))
        self.assertEqual(before, list(Dojo_Group_Member.objects.filter(user=self.foreign).values_list("group_id", "role_id")))

    def test_multi_product_group_tampering_fails_closed(self):
        Product_Group.objects.create(group=self.policy.group, product=self.b, role=self.writer)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_global_role_group_tampering_fails_closed(self):
        Global_Role.objects.create(group=self.policy.group, role=self.writer)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()

    def test_config_permission_group_tampering_fails_closed(self):
        self.policy.group.auth_group.permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="auth"))
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="http://testserver")
    def test_email_invitation_has_real_single_use_setup_token(self):
        target = self.invite()
        request = self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,))).wsgi_request
        send_password_invitation(user=target, request=request)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [target.email])
        self.assertIn(urlsafe_base64_encode(force_bytes(target.pk)), mail.outbox[0].body)
        token = re.search(r"[0-9a-z]+-[0-9a-f]{32}", mail.outbox[0].body).group(0)
        self.assertTrue(default_token_generator.check_token(target, token))
        target.set_unusable_password()
        target.save()
        self.assertFalse(default_token_generator.check_token(target, token))

    def test_native_user_directory_is_denied_even_with_configuration_permission(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="auth"))
        for url in ("/user", reverse("view_user", args=(self.foreign.pk,)), "/api/v2/users/", f"/api/v2/users/{self.foreign.pk}/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (403, 404))
                self.assertNotContains(response, self.foreign.email, status_code=response.status_code)

    def test_group_member_api_never_lists_foreign_members(self):
        response = self.client.get("/api/v2/dojo_group_members/")
        self.assertIn(response.status_code, (200, 403))
        if response.status_code == 200:
            data = response.json()
            for item in data if isinstance(data, list) else data.get("results", []):
                self.assertNotEqual(item.get("user"), self.foreign.pk)

    def test_native_maintainer_cannot_escalate_group_members(self):
        self.membership.role = self.maintainer
        self.membership.save(update_fields=("role",))
        for permission in (Permissions.Group_Edit, Permissions.Group_Manage_Members, Permissions.Group_Member_Delete):
            self.assertFalse(user_has_permission(self.user, self.policy.group, permission))
        response = self.client.post("/api/v2/dojo_group_members/", {"group": self.policy.group_id, "user": self.foreign.pk, "role": self.maintainer.pk})
        self.assertIn(response.status_code, (403, 404))

    def test_native_product_member_route_cannot_create_privileged_bypass(self):
        Product_Group.objects.filter(group=self.policy.group).update(role=self.maintainer)
        response = self.client.post("/api/v2/product_members/", {"product": self.a.pk, "user": self.user.pk, "role": self.maintainer.pk})
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(Product_Member.objects.filter(product=self.a, user=self.user).exists())

    def test_engagement_add_edit_delete_api_are_blocked(self):
        response = self.client.post("/api/v2/engagements/", {"product": self.a.pk, "name": "Forbidden", "target_start": str(date.today()), "target_end": str(date.today())})
        self.assertEqual(response.status_code, 403)
        for method in ("patch", "delete"):
            response = getattr(self.client, method)(f"/api/v2/engagements/{self.engagement.pk}/", data='{"name":"Forbidden"}', content_type="application/json")
            self.assertEqual(response.status_code, 403)
        self.engagement.refresh_from_db()
        self.assertEqual(self.engagement.name, "Existing engagement")

    def test_engagement_copy_get_and_post_are_blocked(self):
        for method in ("get", "post"):
            response = getattr(self.client, method)(reverse("copy_engagement", args=(self.engagement.pk,)))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(Engagement.objects.filter(product=self.a).count(), 1)

    def test_finding_edit_api_still_works(self):
        response = self.client.patch(f"/api/v2/findings/{self.finding.pk}/", data='{"title":"Allowed finding edit"}', content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.title, "Allowed Finding Edit")

    def test_finding_create_and_delete_are_blocked(self):
        self.assertEqual(self.client.post("/api/v2/findings/", {"test": self.test.pk, "title": "Forbidden"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v2/findings/{self.finding.pk}/").status_code, 403)

    def test_test_creation_import_reimport_and_product_mutations_are_blocked(self):
        for url, data in (
            ("/api/v2/tests/", {"engagement": self.engagement.pk}),
            ("/api/v2/import-scan/", {"engagement": self.engagement.pk, "scan_type": "Generic Findings Import"}),
            ("/api/v2/reimport-scan/", {"test": self.test.pk, "scan_type": "Generic Findings Import"}),
            ("/api/v2/product_groups/", {"product": self.a.pk, "group": self.other_policy.group_id, "role": self.writer.pk}),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(self.client.delete(f"/api/v2/products/{self.a.pk}/").status_code, 403)

    def test_product_group_assignment_edit_and_delete_are_blocked(self):
        assignment = Product_Group.objects.get(group=self.policy.group)
        Product_Group.objects.filter(pk=assignment.pk).update(role=self.maintainer)
        for method in ("put", "delete"):
            response = getattr(self.client, method)(f"/api/v2/product_groups/{assignment.pk}/", data='{"role":4}', content_type="application/json")
            self.assertEqual(response.status_code, 403)

    def test_foreign_product_is_not_visible(self):
        self.assertIn(self.client.get(f"/api/v2/products/{self.b.pk}/").status_code, (403, 404))

    def test_admin_retains_native_forms_and_manual_account_assignment(self):
        admin = User.objects.create_superuser(username="test-native-admin", email="admin@example.test")
        self.client.force_login(admin)
        for name, pk in (("edit_group", self.policy.group_id), ("edit_user", self.user.pk)):
            response = self.client.get(reverse(name, args=(pk,)))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Do you want to apply delete/edit restrictions")
        self.assertEqual(self.client.get("/api/v2/users/").status_code, 200)
        response = self.client.post("/api/v2/dojo_group_members/", {"group": self.policy.group_id, "user": self.foreign.pk, "role": self.reader.pk})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(Dojo_Group_Member.objects.filter(group=self.policy.group, user=self.foreign).exists())

    def test_admin_native_group_create_and_edit_post_without_checkbox(self):
        admin = User.objects.create_superuser(username="native-form-admin", email="nativeform@example.test")
        self.client.force_login(admin)
        response = self.client.post(reverse("add_group"), {"name": "Ordinary group", "description": "Created", "role": ""})
        self.assertEqual(response.status_code, 302)
        group = Dojo_Group.objects.get(name="Ordinary group")
        self.assertFalse(RestrictedGroupPolicy.objects.filter(group=group).exists())
        response = self.client.post(reverse("edit_group", args=(group.pk,)), {"name": group.name, "description": "Edited", "role": ""})
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.description, "Edited")
