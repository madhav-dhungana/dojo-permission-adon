from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions, Roles
from dojo.models import (
    Dojo_Group,
    Dojo_Group_Member,
    Global_Role,
    Product,
    Product_Group,
    Product_Member,
    Product_Type,
    Product_Type_Group,
    Role,
    SLA_Configuration,
)

from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.forms import RestrictedDojoGroupForm
from dojo_permission.middleware import RestrictedActionMiddleware
from dojo_permission.policy import policy_denies_permission
from dojo_permission.services import add_existing_user_to_policy, create_invited_user


class RestrictedPolicyTests(TestCase):
    def setUp(self):
        self.writer = Role.objects.get(pk=Roles.Writer)
        self.reader = Role.objects.get(pk=Roles.Reader)
        self.maintainer = Role.objects.get(pk=Roles.Maintainer)
        self.owner = Role.objects.get(pk=Roles.Owner)
        self.product_type = Product_Type.objects.create(name="Permission test type")
        sla = SLA_Configuration.objects.first()
        self.product = Product.objects.create(
            name="Permission test product",
            description="Policy test",
            prod_type=self.product_type,
            sla_configuration=sla,
        )
        self.group = Dojo_Group.objects.create(name="Restricted test group")
        self.policy = RestrictedGroupPolicy.objects.create(group=self.group)
        self.user = get_user_model().objects.create_user(
            username="restricted-writer",
            email="restricted@example.test",
        )
        Dojo_Group_Member.objects.create(
            group=self.group,
            user=self.user,
            role=self.writer,
        )
        Product_Group.objects.create(
            product=self.product,
            group=self.group,
            role=self.writer,
        )

    def test_restricted_writer_can_only_update_existing_findings(self):
        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Edit))
        denied_permissions = (
            Permissions.Engagement_Add,
            Permissions.Engagement_Delete,
            Permissions.Test_Add,
            Permissions.Import_Scan_Result,
            Permissions.Finding_Add,
            Permissions.Finding_Delete,
        )
        for permission in denied_permissions:
            with self.subTest(permission=permission):
                self.assertFalse(user_has_permission(self.user, self.product, permission))

    def test_global_writer_does_not_bypass_restricted_policy(self):
        Global_Role.objects.update_or_create(
            user=self.user,
            defaults={"role": self.writer},
        )

        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Engagement_Add))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Test_Add))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Import_Scan_Result))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Finding_Add))

    def test_maintainer_membership_inside_restricted_group_does_not_bypass_policy(self):
        membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        membership.role = self.maintainer
        membership.save(update_fields=("role",))

        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Engagement_Add))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Test_Add))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Import_Scan_Result))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Finding_Add))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Finding_Delete))

    def test_reader_membership_activates_restricted_policy(self):
        membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        membership.role = self.reader
        membership.save(update_fields=("role",))

        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Edit))
        self.assertTrue(
            policy_denies_permission(self.user, self.product, Permissions.Finding_Delete),
        )

    def test_product_type_group_assignment_activates_policy(self):
        Product_Group.objects.filter(product=self.product, group=self.group).delete()
        Product_Type_Group.objects.create(
            product_type=self.product_type,
            group=self.group,
            role=self.writer,
        )

        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.product, Permissions.Finding_Add))

    def test_inactive_policy_does_not_restrict_permissions(self):
        self.policy.is_active = False
        self.policy.save(update_fields=("is_active", "updated_at"))

        self.assertFalse(
            policy_denies_permission(
                self.user,
                self.product,
                Permissions.Finding_Delete,
            ),
        )

    def test_owner_membership_inside_restricted_group_bypasses_policy(self):
        membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        membership.role = self.owner
        membership.save(update_fields=("role",))

        for permission in (
            Permissions.Engagement_Add,
            Permissions.Engagement_Delete,
            Permissions.Test_Add,
            Permissions.Import_Scan_Result,
            Permissions.Finding_Add,
            Permissions.Finding_Delete,
        ):
            with self.subTest(permission=permission):
                self.assertFalse(policy_denies_permission(self.user, self.product, permission))

        # The overlay is removed for this user; DefectDojo's underlying
        # product-group Writer role still determines which actions are allowed.
        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Engagement_Add))
        self.assertTrue(user_has_permission(self.user, self.product, Permissions.Finding_Add))

    def test_owner_membership_bypasses_duplicate_restricted_membership(self):
        Dojo_Group_Member.objects.create(
            group=self.group,
            user=self.user,
            role=self.owner,
        )

        self.assertFalse(
            policy_denies_permission(
                self.user,
                self.product,
                Permissions.Finding_Delete,
            ),
        )

    def test_superuser_bypasses_policy_and_keeps_user_group_administration(self):
        superuser = get_user_model().objects.create_superuser(
            username="permission-superuser",
            email="superuser@example.test",
        )
        Dojo_Group_Member.objects.create(
            group=self.group,
            user=superuser,
            role=self.maintainer,
        )

        for permission in (
            Permissions.Engagement_Add,
            Permissions.Engagement_Delete,
            Permissions.Test_Add,
            Permissions.Import_Scan_Result,
            Permissions.Finding_Add,
            Permissions.Finding_Delete,
        ):
            with self.subTest(permission=permission):
                self.assertFalse(policy_denies_permission(superuser, self.product, permission))
                self.assertTrue(user_has_permission(superuser, self.product, permission))
        self.assertTrue(user_has_permission(superuser, self.group, Permissions.Group_Edit))
        self.assertTrue(user_has_permission(superuser, self.group, Permissions.Group_Manage_Members))
        self.assertTrue(superuser.has_perm("auth.change_user"))

    def test_superuser_can_open_group_and_user_edit_pages_with_checkbox(self):
        superuser = get_user_model().objects.create_superuser(
            username="ui-superuser",
            email="ui-superuser@example.test",
        )
        self.client.force_login(superuser)

        group_response = self.client.get(reverse("edit_group", args=(self.group.id,)))
        self.assertEqual(group_response.status_code, 200)
        self.assertContains(
            group_response,
            "Do you want to apply delete/edit restrictions to this group?",
        )
        self.assertFalse(
            group_response.context["form"].fields["apply_restrictions"].disabled,
        )
        self.assertEqual(
            tuple(group_response.context["form"].fields),
            ("name", "description", "apply_restrictions"),
        )

        user_response = self.client.get(reverse("edit_user", args=(self.user.id,)))
        self.assertEqual(user_response.status_code, 200)

    def test_group_overlay_preserves_defectdojo_group_pages(self):
        from dojo.group import views as group_views

        RestrictedActionMiddleware(lambda request: HttpResponse())

        self.assertEqual(group_views.ListGroups().get_template(), "dojo/groups.html")
        self.assertEqual(group_views.ViewGroup().get_template(), "dojo/view_group.html")
        self.assertEqual(group_views.AddGroup().get_template(), "dojo/add_group.html")
        self.assertEqual(group_views.EditGroup().get_template(), "dojo/add_group.html")

    def test_restricted_member_can_open_limited_add_or_invite_page_directly(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse(
            "dojo_permission:manage_policy_members",
            args=(self.policy.id,),
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add an existing account")
        self.assertContains(response, "Create or invite an account")

    def test_restricted_member_group_link_opens_limited_member_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("view_group", args=(self.group.id,)))

        self.assertRedirects(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.policy.id,)),
        )

    def test_restricted_member_can_add_existing_account_through_limited_page(self):
        target = get_user_model().objects.create_user(
            username="existing-page-user",
            email="existing-page-user@example.test",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dojo_permission:manage_policy_members", args=(self.policy.id,)),
            {
                "action": "add_existing",
                "existing-user": target.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.policy.id,)),
        )
        membership = Dojo_Group_Member.objects.get(group=self.group, user=target)
        self.assertEqual(membership.role_id, self.writer.id)

    def test_restricted_member_can_create_sso_account_through_limited_page(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dojo_permission:manage_policy_members", args=(self.policy.id,)),
            {
                "action": "invite_new",
                "invite-username": "sso-page-user",
                "invite-email": "sso-page-user@example.test",
                "invite-first_name": "SSO",
                "invite-last_name": "User",
                "invite-authentication_method": "sso",
            },
        )

        self.assertRedirects(
            response,
            reverse("dojo_permission:manage_policy_members", args=(self.policy.id,)),
        )
        invited = get_user_model().objects.get(username="sso-page-user")
        self.assertFalse(invited.has_usable_password())
        membership = Dojo_Group_Member.objects.get(group=self.group, user=invited)
        self.assertEqual(membership.role_id, self.writer.id)

    def test_superuser_keeps_native_group_page(self):
        superuser = get_user_model().objects.create_superuser(
            username="native-group-superuser",
            email="native-group-superuser@example.test",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("view_group", args=(self.group.id,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dojo/view_group.html")

    def test_restricted_group_maintainer_can_delegate_same_group_role(self):
        requester_membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        requester_membership.role = self.maintainer
        requester_membership.save(update_fields=("role",))
        target = get_user_model().objects.create_user(
            username="maintainer-delegated-user",
            email="maintainer-delegated@example.test",
        )

        result = add_existing_user_to_policy(
            requester=self.user,
            target_user=target,
            policy=self.policy,
        )

        self.assertTrue(result.created)
        self.assertEqual(result.membership.role_id, self.maintainer.id)
        self.assertFalse(user_has_permission(target, self.product, Permissions.Finding_Add))
        self.assertTrue(user_has_permission(target, self.product, Permissions.Finding_Edit))

    def test_restricted_group_owner_does_not_receive_limited_delegation_flow(self):
        requester_membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        requester_membership.role = self.owner
        requester_membership.save(update_fields=("role",))
        target = get_user_model().objects.create_user(
            username="owner-target-user",
            email="owner-target@example.test",
        )

        with self.assertRaises(PermissionDenied):
            add_existing_user_to_policy(
                requester=self.user,
                target_user=target,
                policy=self.policy,
            )

    def test_independent_maintainer_role_is_not_restricted(self):
        Product_Member.objects.create(
            product=self.product,
            user=self.user,
            role=self.maintainer,
        )
        privileged_permissions = (
            Permissions.Engagement_Add,
            Permissions.Engagement_Delete,
            Permissions.Test_Add,
            Permissions.Import_Scan_Result,
            Permissions.Finding_Add,
            Permissions.Finding_Delete,
        )
        for permission in privileged_permissions:
            with self.subTest(permission=permission):
                self.assertTrue(user_has_permission(self.user, self.product, permission))

    def test_copy_finding_route_is_denied_by_finding_add_policy(self):
        request = RequestFactory().get("/finding/123/copy")
        request.user = self.user
        finding = object()

        def copy_finding(request, fid):
            return HttpResponse()

        copy_finding.__module__ = "dojo.finding.views"
        middleware = RestrictedActionMiddleware(lambda request: HttpResponse())
        with (
            patch("dojo_permission.middleware.get_object_or_404", return_value=finding),
            patch("dojo_permission.middleware.policy_denies_permission", return_value=True) as deny,
            self.assertRaises(PermissionDenied),
        ):
            middleware.process_view(request, copy_finding, (), {"fid": 123})

        deny.assert_called_once_with(request.user, finding, Permissions.Finding_Add)

    def test_unrelated_copy_finding_function_is_not_intercepted(self):
        request = RequestFactory().get("/custom/copy")
        request.user = self.user

        def copy_finding(request, fid):
            return HttpResponse("custom")

        middleware = RestrictedActionMiddleware(lambda request: HttpResponse())
        with patch("dojo_permission.middleware.get_object_or_404") as lookup:
            response = middleware.process_view(request, copy_finding, (), {"fid": 123})

        self.assertIsNone(response)
        lookup.assert_not_called()

    def test_added_existing_user_inherits_group_role_only(self):
        other_group = Dojo_Group.objects.create(name="Other group")
        target = get_user_model().objects.create_user(
            username="existing-user",
            email="existing@example.test",
        )
        Dojo_Group_Member.objects.create(
            group=other_group,
            user=target,
            role=self.maintainer,
        )

        result = add_existing_user_to_policy(
            requester=self.user,
            target_user=target,
            policy=self.policy,
        )

        self.assertTrue(result.created)
        self.assertEqual(result.membership.role_id, self.writer.id)
        self.assertEqual(
            Dojo_Group_Member.objects.get(group=other_group, user=target).role_id,
            self.maintainer.id,
        )

    def test_created_sso_user_has_unusable_password_and_inherited_group(self):
        invited = create_invited_user(
            requester=self.user,
            policy=self.policy,
            username="sso-user",
            email="sso@example.test",
        )

        self.assertFalse(invited.has_usable_password())
        membership = Dojo_Group_Member.objects.get(group=self.group, user=invited)
        self.assertEqual(membership.role_id, self.writer.id)

    def test_group_form_checkbox_activates_and_deactivates_policy(self):
        create_form = RestrictedDojoGroupForm(data={
            "name": "Checkbox restricted group",
            "description": "Created through the DefectDojo group form",
            "apply_restrictions": "on",
        })
        self.assertTrue(create_form.is_valid(), create_form.errors)
        group = create_form.save()
        policy = RestrictedGroupPolicy.objects.get(group=group)
        self.assertTrue(policy.is_active)

        policy_admin = get_user_model().objects.create_superuser(
            username="checkbox-policy-admin",
            email="checkbox-policy-admin@example.test",
        )

        edit_form = RestrictedDojoGroupForm(data={
            "name": group.name,
            "description": group.description,
        }, instance=group, policy_actor=policy_admin)
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        edit_form.save()
        policy.refresh_from_db()
        self.assertFalse(policy.is_active)

    def test_group_form_shows_active_policy_as_checked(self):
        form = RestrictedDojoGroupForm(instance=self.group)

        self.assertTrue(form.fields["apply_restrictions"].initial)

    def test_group_form_without_actor_cannot_disable_existing_policy(self):
        form = RestrictedDojoGroupForm(
            data={
                "name": self.group.name,
                "description": self.group.description,
            },
            instance=self.group,
        )

        self.assertTrue(form.fields["apply_restrictions"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.is_active)

    def test_restricted_maintainer_cannot_disable_group_policy(self):
        membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        membership.role = self.maintainer
        membership.save(update_fields=("role",))
        form = RestrictedDojoGroupForm(
            data={
                "name": self.group.name,
                "description": self.group.description,
            },
            instance=self.group,
            policy_actor=self.user,
        )

        self.assertTrue(form.fields["apply_restrictions"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.is_active)

    def test_restricted_group_owner_can_disable_group_policy(self):
        membership = Dojo_Group_Member.objects.get(group=self.group, user=self.user)
        membership.role = self.owner
        membership.save(update_fields=("role",))
        form = RestrictedDojoGroupForm(
            data={
                "name": self.group.name,
                "description": self.group.description,
            },
            instance=self.group,
            policy_actor=self.user,
        )

        self.assertFalse(form.fields["apply_restrictions"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.is_active)
