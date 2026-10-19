"""One shared restriction group, independent product access, and legacy upgrades."""

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions, Roles
from dojo.models import (
    Dojo_Group, Dojo_Group_Member, Engagement, Finding, Global_Role, Product,
    Product_Group, Product_Member, Product_Type_Group, Product_Type_Member, Role,
    System_Settings, UserContactInfo,
)
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.policy import policy_denies_permission
from dojo_permission.provisioning import (
    ManagedGroupCollision, ensure_default_managed_group, reconcile_managed_groups,
)
from dojo_permission.services import (
    invitation_policies_for_user,
    InvitationEmailError, add_existing_user_to_policy, create_invited_user,
    invitable_product_memberships, requester_policy_membership,
    send_password_invitation,
)
from dojo_permission.tests.fixtures import SingleGroupFixture

User = get_user_model()
DENIED = (
    Permissions.Engagement_Add, Permissions.Engagement_Edit, Permissions.Engagement_Delete,
    Permissions.Test_Add, Permissions.Import_Scan_Result, Permissions.Finding_Add,
    Permissions.Finding_Delete, Permissions.Product_Delete, Permissions.Product_Group_Add,
    Permissions.Product_Group_Edit, Permissions.Product_Group_Delete,
)


class SingleGroupPolicyTests(SingleGroupFixture, TestCase):
    def setUp(self):
        self.build_fixture()

    def invite(self, **kwargs):
        values = dict(requester=self.user, policy=self.policy,
                      username="invited-new", email="invited-new@example.test")
        values.update(kwargs)
        return create_invited_user(**values)

    def test_product_creation_keeps_one_access_free_shared_policy(self):
        self.assertTrue(self.policy.is_active)
        self.assertTrue(self.policy.is_default)
        self.assertIsNone(self.policy.product_id)
        self.assertEqual(self.policy.group.name, "Dev Team group")
        self.assertFalse(RestrictedGroupPolicy.objects.filter(product__in=(self.a, self.b, self.c)).exists())
        self.assertFalse(Product_Group.objects.filter(group=self.policy.group).exists())
        self.assertFalse(Product_Type_Group.objects.filter(group=self.policy.group).exists())
        self.assertFalse(Global_Role.objects.filter(group=self.policy.group, role__isnull=False).exists())
        self.assertFalse(self.policy.group.auth_group.permissions.exists())
        for product in (self.a, self.b, self.c):
            self.assertFalse(Dojo_Group.objects.filter(name__endswith=f"- Product {product.pk}").exists())

    def test_post_migrate_preserves_shared_identity_and_native_grants(self):
        before = list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk"))
        group_id, policy_id = self.policy.group_id, self.policy.pk
        call_command("migrate", "dojo_permission", interactive=False, verbosity=0)
        marker = ensure_default_managed_group()
        self.assertEqual((marker.group_id, marker.pk), (group_id, policy_id))
        self.assertEqual(before, list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk")))
        self.assertFalse(RestrictedGroupPolicy.objects.filter(product__isnull=False).exists())

    def test_product_created_during_a_user_request_does_not_create_another_group(self):
        from crum import impersonate

        before = list(Dojo_Group_Member.objects.filter(user=self.user).values_list("group_id", "role_id"))
        with impersonate(self.user):
            product = self.product("Request-created P4")
        self.assertFalse(RestrictedGroupPolicy.objects.filter(product=product).exists())
        self.assertFalse(Dojo_Group.objects.filter(name__endswith=f"- Product {product.pk}").exists())
        self.assertEqual(before, list(Dojo_Group_Member.objects.filter(user=self.user).values_list("group_id", "role_id")))

    def test_shared_group_does_not_grant_a_product_by_itself(self):
        user = self.account("enrolled-without-product")
        for product in (self.a, self.b, self.c):
            self.assertFalse(user_has_permission(user, product, Permissions.Product_View))
            self.assertFalse(user_has_permission(user, product, Permissions.Finding_Edit))

    def test_group_reader_writer_and_maintainer_all_receive_restrictions(self):
        for role in (self.reader, self.writer, self.maintainer):
            self.membership.role = role
            self.membership.save(update_fields=("role",))
            for permission in DENIED:
                with self.subTest(role=role, permission=permission):
                    self.assertTrue(policy_denies_permission(self.user, self.a, permission))
                    self.assertFalse(user_has_permission(self.user, self.a, permission))
            self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))

    def test_native_product_reader_can_invite_without_promoting_the_child(self):
        Product_Member.objects.filter(product=self.a, user=self.user).update(role=self.reader)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Product_View))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Finding_Edit))
        target = self.invite()
        self.assertEqual(Product_Member.objects.get(product=self.a, user=target).role_id, self.reader.pk)
        self.assertTrue(user_has_permission(target, self.a, Permissions.Product_View))
        self.assertFalse(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.a, Permissions.Engagement_Edit))

    def test_owner_and_duplicate_owner_memberships_keep_native_flow(self):
        Dojo_Group_Member.objects.create(group=self.policy.group, user=self.user, role=self.owner)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        with self.assertRaises(PermissionDenied):
            requester_policy_membership(self.user, self.policy)
        self.assertEqual(self.client.get(reverse("dojo_permission:invitation_entry")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("product")), 'id="dojo-permission-create-account"')

    def test_direct_product_maintainer_role_is_preserved_but_restricted(self):
        Product_Member.objects.filter(product=self.a, user=self.user).update(role=self.maintainer)
        self.assertEqual(Product_Member.objects.get(product=self.a, user=self.user).role_id, self.maintainer.pk)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Finding_Delete))

    def test_global_maintainer_does_not_escape_shared_scope_but_owner_is_exempt(self):
        Global_Role.objects.create(user=self.user, role=self.maintainer)
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Product_View))
        self.assertEqual(Global_Role.objects.get(user=self.user).role_id, self.maintainer.pk)
        Global_Role.objects.filter(user=self.user).update(role=self.owner)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertTrue(user_has_permission(self.user, self.b, Permissions.Product_View))

    def test_other_group_global_maintainer_does_not_escape_but_global_owner_is_exempt(self):
        other = Dojo_Group.objects.create(name="Separate global role group")
        Dojo_Group_Member.objects.create(group=other, user=self.user, role=self.reader)
        grant = Global_Role.objects.create(group=other, role=self.maintainer)
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Product_View))
        grant.role = self.owner
        grant.save(update_fields=("role",))
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertTrue(user_has_permission(self.user, self.b, Permissions.Product_View))

    def test_direct_product_owner_is_native_only_on_its_assigned_product(self):
        Product_Member.objects.filter(product=self.a, user=self.user).update(role=self.owner)
        Product_Member.objects.create(product=self.b, user=self.user, role=self.writer)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.c, Permissions.Product_View))
        with self.assertRaises(PermissionDenied):
            self.invite()

    def test_other_group_and_product_type_grants_do_not_expand_shared_scope(self):
        unrelated = Dojo_Group.objects.create(name="Independent broad access")
        Dojo_Group_Member.objects.create(group=unrelated, user=self.user, role=self.owner)
        Product_Group.objects.create(group=unrelated, product=self.b, role=self.owner)
        Product_Type_Member.objects.create(product_type=self.c.prod_type, user=self.user, role=self.owner)
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Product_View))
        self.assertFalse(user_has_permission(self.user, self.c, Permissions.Product_View))
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))

    def test_superuser_keeps_native_account_and_group_administration(self):
        admin = User.objects.create_superuser(username="native-admin", email="native-admin@example.test")
        Dojo_Group_Member.objects.create(group=self.policy.group, user=admin, role=self.reader)
        self.client.force_login(admin)
        for name, pk in (("edit_group", self.policy.group_id), ("edit_user", self.user.pk)):
            response = self.client.get(reverse(name, args=(pk,)))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Do you want to apply delete/edit restrictions")
        self.assertEqual(self.client.get("/api/v2/users/").status_code, 200)
        self.assertTrue(user_has_permission(admin, self.a, Permissions.Engagement_Edit))

    def test_admin_can_manually_add_existing_account_and_explicit_product_access(self):
        admin = User.objects.create_superuser(username="manual-assignment-admin", email="manual-assignment-admin@example.test")
        target = User.objects.create_user(username="existing-manual-target", email="existing-manual-target@example.test")
        self.client.force_login(admin)
        self.assertEqual(self.client.post("/api/v2/dojo_group_members/", {"group": self.policy.group_id, "user": target.pk, "role": self.reader.pk}).status_code, 201)
        self.assertFalse(user_has_permission(target, self.a, Permissions.Product_View))
        response = self.client.post("/api/v2/product_members/", {"product": self.a.pk, "user": target.pk, "role": self.writer.pk})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_native_group_forms_still_work_without_the_removed_checkbox(self):
        admin = User.objects.create_superuser(username="native-form-admin", email="native-form-admin@example.test")
        self.client.force_login(admin)
        response = self.client.post(reverse("add_group"), {"name": "Ordinary native group", "description": "Created", "role": ""})
        self.assertEqual(response.status_code, 302)
        group = Dojo_Group.objects.get(name="Ordinary native group")
        self.assertFalse(RestrictedGroupPolicy.objects.filter(group=group).exists())
        self.assertEqual(self.client.post(reverse("edit_group", args=(group.pk,)), {"name": group.name, "description": "Edited", "role": ""}).status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.description, "Edited")

    def test_native_user_menu_discovers_the_one_invitation_scope(self):
        response = self.client.get(reverse("product"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dojo-permission-create-account"')
        entry = self.client.get(reverse("dojo_permission:invitation_entry"))
        self.assertRedirects(entry, reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), fetch_redirect_response=False)

    def test_invitation_form_read_only_lists_all_direct_products_with_exact_roles(self):
        Product_Member.objects.create(product=self.b, user=self.user, role=self.reader)
        Product_Member.objects.create(product=self.c, user=self.user, role=self.maintainer)
        response = self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("product", response.context["invitation_form"].fields)
        self.assertNotIn("products", response.context["invitation_form"].fields)
        self.assertNotIn("role", response.context["invitation_form"].fields)
        self.assertNotContains(response, 'name="invite-product"')
        self.assertNotContains(response, 'name="invite-products"')
        self.assertEqual(
            {(row.product_id, row.role_id) for row in response.context["product_memberships"]},
            {(self.a.pk, self.writer.pk), (self.b.pk, self.reader.pk), (self.c.pk, self.maintainer.pk)},
        )
        for text in (self.a.name, self.b.name, self.c.name, self.reader.name, self.writer.name, self.maintainer.name):
            self.assertContains(response, text)
        for text in ("Add an existing account", "existing-user", self.foreign.username, self.foreign.email, self.third.email):
            self.assertNotContains(response, text)

    def test_new_account_gets_only_shared_group_and_every_direct_product_with_unchanged_roles(self):
        Product_Member.objects.create(product=self.b, user=self.user, role=self.reader)
        Product_Member.objects.create(product=self.c, user=self.user, role=self.maintainer)
        target = self.invite()
        self.assertFalse(target.has_usable_password())
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", "role_id")), [(self.policy.group_id, self.reader.pk)])
        self.assertEqual(
            set(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")),
            {(self.a.pk, self.writer.pk), (self.b.pk, self.reader.pk), (self.c.pk, self.maintainer.pk)},
        )
        self.assertFalse(Global_Role.objects.filter(user=target).exists())
        self.assertTrue(user_has_permission(target, self.a, Permissions.Finding_Edit))
        self.assertTrue(user_has_permission(target, self.b, Permissions.Product_View))
        self.assertFalse(user_has_permission(target, self.b, Permissions.Finding_Edit))
        self.assertTrue(user_has_permission(target, self.c, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(target, self.c, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(target, self.c, Permissions.Finding_Delete))

    def test_group_role_inheritance_does_not_add_product_permissions(self):
        for role in (self.reader, self.writer, self.maintainer):
            self.membership.role = role
            self.membership.save(update_fields=("role",))
            target = self.invite(username=f"inherited-{role.pk}", email=f"inherited-{role.pk}@example.test")
            self.assertEqual(Dojo_Group_Member.objects.get(user=target, group=self.policy.group).role_id, role.pk)
            self.assertFalse(user_has_permission(target, self.a, Permissions.Engagement_Edit))
            self.assertFalse(user_has_permission(target, self.a, Permissions.Finding_Delete))

    def test_selected_product_service_argument_is_rejected_not_used_as_a_subset(self):
        Product_Member.objects.create(product=self.c, user=self.user, role=self.reader)
        for product in (self.a, self.b, self.c):
            with self.subTest(product=product), self.assertRaises((PermissionDenied, ValidationError)):
                self.invite(product=product)
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_posted_product_subset_foreign_product_or_role_override_creates_no_account(self):
        Product_Member.objects.create(product=self.c, user=self.user, role=self.reader)
        for index, forged in enumerate((
            {"invite-product": self.a.pk}, {"invite-products": [self.a.pk]},
            {"product": self.b.pk}, {"products": [self.b.pk, self.c.pk]},
            {"role": self.owner.pk}, {"invite-role": self.owner.pk},
        )):
            username = f"forged-invitation-{index}"
            response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
                "action": "invite_new", "invite-username": username,
                "invite-email": f"{username}@example.test", "invite-authentication_method": "sso",
                **forged,
            })
            with self.subTest(forged=forged):
                self.assertIn(response.status_code, (400, 403))
                self.assertFalse(User.objects.filter(username=username).exists())

    def test_no_direct_products_blocks_service_page_navigation_and_account_creation(self):
        Product_Member.objects.filter(user=self.user).delete()
        with self.assertRaises(PermissionDenied):
            invitable_product_memberships(self.user, self.policy)
        with self.assertRaises(PermissionDenied):
            self.invite()
        self.assertEqual(self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,))).status_code, 403)
        self.assertEqual(self.client.get(reverse("dojo_permission:invitation_entry")).status_code, 403)
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_owner_or_unknown_role_in_any_direct_product_denies_the_whole_invitation(self):
        unknown_role = Role.objects.create(name="Unsupported delegated role")
        extra = Product_Member.objects.create(product=self.b, user=self.user, role=self.owner)
        for role in (self.owner, unknown_role):
            extra.role = role
            extra.save(update_fields=("role",))
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                invitable_product_memberships(self.user, self.policy)
            with self.assertRaises(PermissionDenied):
                self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_inherited_membership_product_ids_are_not_hard_coded(self):
        later = self.product("Arbitrary later product")
        Product_Member.objects.filter(user=self.user).delete()
        Product_Member.objects.create(product=later, user=self.user, role=self.reader)
        rows = invitable_product_memberships(self.user, self.policy)
        self.assertIsInstance(rows, tuple)
        self.assertEqual([(row.product_id, row.role_id) for row in rows], [(later.pk, self.reader.pk)])
        target = self.invite()
        self.assertEqual(list(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")), [(later.pk, self.reader.pk)])
        self.assertFalse(user_has_permission(target, self.a, Permissions.Product_View))

    def test_duplicate_identical_direct_grants_are_copied_once(self):
        Product_Member.objects.create(product=self.a, user=self.user, role=self.writer)
        target = self.invite()
        self.assertEqual(list(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")), [(self.a.pk, self.writer.pk)])

    def test_conflicting_duplicate_direct_grants_reject_the_entire_invitation(self):
        Product_Member.objects.create(product=self.a, user=self.user, role=self.reader)
        with self.assertRaises(PermissionDenied):
            self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_existing_account_assignment_is_admin_only_without_changing_existing_roles(self):
        before = list(Product_Member.objects.filter(user=self.foreign).values_list("pk", "product_id", "role_id"))
        with self.assertRaises(PermissionDenied):
            add_existing_user_to_policy(requester=self.user, target_user=self.foreign, policy=self.policy)
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {"action": "add_existing", "existing-user": self.foreign.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(before, list(Product_Member.objects.filter(user=self.foreign).values_list("pk", "product_id", "role_id")))
        self.assertFalse(Product_Member.objects.filter(user=self.foreign, product=self.a).exists())

    def test_sso_form_creates_account_with_all_direct_product_roles_without_selector(self):
        Product_Member.objects.create(product=self.c, user=self.user, role=self.reader)
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
            "action": "invite_new",
            "invite-username": "sso-page-new", "invite-email": "sso-page@example.test",
            "invite-authentication_method": "sso",
        })
        self.assertEqual(response.status_code, 302)
        target = User.objects.get(username="sso-page-new")
        self.assertFalse(target.has_usable_password())
        self.assertEqual(set(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")), {(self.a.pk, self.writer.pk), (self.c.pk, self.reader.pk)})

    def test_invalid_username_form_creates_no_account_or_memberships(self):
        before = (User.objects.count(), Dojo_Group_Member.objects.count(), Product_Member.objects.count())
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
            "action": "invite_new", "invite-username": "invalid/name",
            "invite-email": "invalid-name@example.test", "invite-authentication_method": "sso",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("username", response.context["invitation_form"].errors)
        self.assertFalse(User.objects.filter(username="invalid/name").exists())
        self.assertEqual(before, (User.objects.count(), Dojo_Group_Member.objects.count(), Product_Member.objects.count()))

    def test_direct_invitation_service_validates_username_before_creating_any_state(self):
        before = (User.objects.count(), UserContactInfo.objects.count(), Dojo_Group_Member.objects.count(), Product_Member.objects.count())
        with self.assertRaises(ValidationError):
            self.invite(username="invalid/name", email="invalid-service@example.test")
        self.assertEqual(before, (User.objects.count(), UserContactInfo.objects.count(), Dojo_Group_Member.objects.count(), Product_Member.objects.count()))

    def test_valid_unicode_username_is_preserved_with_exact_native_grants(self):
        username = "équipe_招待"
        response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
            "action": "invite_new", "invite-username": username,
            "invite-email": "unicode-team@example.test", "invite-authentication_method": "sso",
        })
        self.assertEqual(response.status_code, 302)
        target = User.objects.get(username=username)
        self.assertEqual(target.username, username)
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", "role_id")), [(self.policy.group_id, self.reader.pk)])
        self.assertEqual(list(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")), [(self.a.pk, self.writer.pk)])

    def test_duplicate_account_is_not_modified_or_identified(self):
        Product_Member.objects.create(product=self.c, user=self.user, role=self.maintainer)
        groups_before = list(Dojo_Group_Member.objects.values_list("pk", "group_id", "user_id", "role_id").order_by("pk"))
        products_before = list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk"))
        with self.assertRaisesMessage(ValidationError, "Unable to create this account."):
            self.invite(username=self.foreign.username, email=self.foreign.email)
        self.assertEqual(groups_before, list(Dojo_Group_Member.objects.values_list("pk", "group_id", "user_id", "role_id").order_by("pk")))
        self.assertEqual(products_before, list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk")))
        self.assertFalse(Product_Member.objects.filter(user=self.foreign, product=self.a).exists())
        self.assertEqual(Dojo_Group_Member.objects.get(group=self.policy.group, user=self.foreign).role_id, self.reader.pk)

    def test_second_product_copy_integrity_failure_rolls_back_the_whole_account(self):
        Product_Member.objects.create(product=self.c, user=self.user, role=self.maintainer)
        groups_before = list(Dojo_Group_Member.objects.values_list("pk", "group_id", "user_id", "role_id").order_by("pk"))
        products_before = list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk"))
        contacts_before = UserContactInfo.objects.count()
        original_create = Product_Member.objects.create
        copied = []

        def fail_second_copy(**kwargs):
            if copied:
                raise IntegrityError("Injected duplicate during the second product copy")
            row = original_create(**kwargs)
            copied.append(row.pk)
            return row

        with patch("dojo_permission.services.Product_Member.objects.create", side_effect=fail_second_copy):
            with self.assertRaisesMessage(ValidationError, "Unable to create this account."):
                self.invite()
        self.assertEqual(len(copied), 1)
        self.assertFalse(User.objects.filter(username="invited-new").exists())
        self.assertEqual(UserContactInfo.objects.count(), contacts_before)
        self.assertEqual(groups_before, list(Dojo_Group_Member.objects.values_list("pk", "group_id", "user_id", "role_id").order_by("pk")))
        self.assertEqual(products_before, list(Product_Member.objects.values_list("pk", "product_id", "user_id", "role_id").order_by("pk")))

    def test_custom_creation_signal_cannot_grant_an_invitee_a_global_role(self):
        before = (User.objects.count(), Global_Role.objects.count(), Product_Member.objects.count())

        def custom_default_role(sender, instance, created, **kwargs):
            if created and instance.username == "invited-new":
                Global_Role.objects.create(user=instance, role=self.owner)

        post_save.connect(custom_default_role, sender=User, weak=False)
        try:
            with self.assertRaises(PermissionDenied):
                self.invite()
        finally:
            post_save.disconnect(custom_default_role, sender=User)
        self.assertFalse(User.objects.filter(username="invited-new").exists())
        self.assertEqual(before, (User.objects.count(), Global_Role.objects.count(), Product_Member.objects.count()))

    def test_configured_default_group_cannot_escape_new_account_scope(self):
        default = Dojo_Group.objects.create(name="Unrelated default group")
        Product_Group.objects.create(group=default, product=self.b, role=self.maintainer)
        settings_row = System_Settings.objects.get()
        settings_row.default_group = default
        settings_row.default_group_role = self.maintainer
        settings_row.save()
        target = self.invite()
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", flat=True)), [self.policy.group_id])
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_shared_product_grant_tampering_fails_closed(self):
        Product_Group.objects.create(group=self.policy.group, product=self.b, role=self.maintainer)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())
        self.assertTrue(Product_Group.objects.filter(group=self.policy.group, product=self.b, role=self.maintainer).exists())

    def test_shared_global_or_config_grants_fail_closed(self):
        Global_Role.objects.create(group=self.policy.group, role=self.writer)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()
        Global_Role.objects.filter(group=self.policy.group).delete()
        self.policy.group.auth_group.permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="auth"))
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    def test_shared_identity_provider_binding_cannot_delegate(self):
        self.policy.group.social_provider = Dojo_Group.AZURE
        self.policy.group.save(update_fields=("social_provider",))
        self.assertEqual(invitation_policies_for_user(self.user), ())
        with self.assertRaises(PermissionDenied):
            self.invite()
        self.assertFalse(User.objects.filter(username="invited-new").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="http://testserver")
    def test_email_invitation_has_a_real_single_use_setup_token(self):
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

    def test_email_failure_preserves_scoped_account_without_claiming_email_success(self):
        with patch("dojo_permission.views.send_password_invitation", side_effect=InvitationEmailError):
            response = self.client.post(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)), {
                "action": "invite_new",
                "invite-username": "email-page-new", "invite-email": "email-page@example.test",
                "invite-authentication_method": "email",
            }, follow=True)
        self.assertContains(response, "setup email could not be sent")
        self.assertNotContains(response, "password setup email was sent")
        target = User.objects.get(username="email-page-new")
        self.assertTrue(Product_Member.objects.filter(user=target, product=self.a, role=self.writer).exists())

    def test_native_user_directory_is_denied_even_with_configuration_permission(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="auth"))
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)
        self.assertEqual(self.client.get(reverse("view_user", args=(self.foreign.pk,))).status_code, 403)
        self.assertEqual(self.client.get("/api/v2/users/").status_code, 403)

    def test_shared_group_member_api_never_lists_foreign_product_accounts(self):
        response = self.client.get("/api/v2/dojo_group_members/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.foreign.pk, [item["user"] for item in response.json()["results"]])
        self.assertNotIn(self.third.pk, [item["user"] for item in response.json()["results"]])

    def test_native_group_maintainer_cannot_escalate_members(self):
        self.membership.role = self.maintainer
        self.membership.save(update_fields=("role",))
        for permission in (Permissions.Group_Edit, Permissions.Group_Manage_Members, Permissions.Group_Member_Delete):
            self.assertFalse(user_has_permission(self.user, self.policy.group, permission))
        response = self.client.post("/api/v2/dojo_group_members/", {"group": self.policy.group_id, "user": self.foreign.pk, "role": self.owner.pk})
        self.assertIn(response.status_code, (403, 404))

    def test_engagement_api_add_edit_delete_and_copy_are_blocked(self):
        self.assertEqual(self.client.post("/api/v2/engagements/", {"product": self.a.pk}).status_code, 403)
        for method in ("patch", "delete"):
            response = getattr(self.client, method)(f"/api/v2/engagements/{self.engagement.pk}/", data='{"name":"Forbidden"}', content_type="application/json")
            self.assertEqual(response.status_code, 403)
        for method in ("get", "post"):
            self.assertEqual(getattr(self.client, method)(reverse("copy_engagement", args=(self.engagement.pk,))).status_code, 403)
        self.engagement.refresh_from_db()
        self.assertEqual(self.engagement.name, "Existing engagement P1")

    def test_finding_edit_api_still_works_while_add_delete_and_copy_are_blocked(self):
        response = self.client.patch(f"/api/v2/findings/{self.finding.pk}/", data='{"title":"Allowed finding edit"}', content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.title, "Allowed Finding Edit")
        self.assertEqual(self.client.post("/api/v2/findings/", {"test": self.test.pk}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v2/findings/{self.finding.pk}/").status_code, 403)
        self.assertEqual(self.client.get(reverse("copy_finding", args=(self.finding.pk,))).status_code, 403)

    def test_test_add_copy_import_and_reimport_are_blocked(self):
        for url, data in (
            ("/api/v2/tests/", {"engagement": self.engagement.pk}),
            ("/api/v2/import-scan/", {"engagement": self.engagement.pk, "scan_type": "Generic Findings Import"}),
            ("/api/v2/reimport-scan/", {"test": self.test.pk, "scan_type": "Generic Findings Import"}),
        ):
            self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self.client.get(reverse("copy_test", args=(self.test.pk,))).status_code, 403)

    def test_product_deletion_group_assignment_and_privileged_member_bypasses_are_blocked(self):
        self.assertEqual(self.client.delete(f"/api/v2/products/{self.a.pk}/").status_code, 403)
        for url, data in (
            ("/api/v2/product_groups/", {"product": self.a.pk, "group": self.policy.group_id, "role": self.maintainer.pk}),
            ("/api/v2/product_members/", {"product": self.a.pk, "user": self.foreign.pk, "role": self.maintainer.pk}),
        ):
            self.assertEqual(self.client.post(url, data).status_code, 403)
        native = Product_Member.objects.get(product=self.a, user=self.user)
        response = self.client.put(f"/api/v2/product_members/{native.pk}/", data='{"role":4}', content_type="application/json")
        self.assertEqual(response.status_code, 403)
        native.refresh_from_db()
        self.assertEqual(native.role_id, self.writer.pk)

    def test_navigation_tag_tolerates_missing_request(self):
        from dojo_permission.templatetags.dojo_permission_tags import dojo_permission_can_invite
        self.assertFalse(dojo_permission_can_invite(None))
        self.assertFalse(dojo_permission_can_invite(""))


class NestedSingleGroupIsolationTests(SingleGroupFixture, TestCase):
    def setUp(self):
        self.build_fixture()
        Product_Member.objects.create(product=self.c, user=self.user, role=self.maintainer)
        self.e = create_invited_user(requester=self.user, policy=self.policy, username="member-e", email="member-e@example.test")
        self.f = create_invited_user(requester=self.e, policy=self.policy, username="member-f", email="member-f@example.test")

    def test_a_e_f_share_one_group_and_inherit_all_p1_p3_roles_without_p2(self):
        for user in (self.user, self.e, self.f):
            self.assertEqual(list(Dojo_Group_Member.objects.filter(user=user).values_list("group_id", flat=True)), [self.policy.group_id])
            self.assertEqual(set(Product_Member.objects.filter(user=user).values_list("product_id", "role_id")), {(self.a.pk, self.writer.pk), (self.c.pk, self.maintainer.pk)})
            self.assertFalse(Global_Role.objects.filter(user=user).exists())
            for product in (self.a, self.c):
                self.assertTrue(user_has_permission(user, product, Permissions.Finding_Edit))
                self.assertFalse(user_has_permission(user, product, Permissions.Engagement_Edit))
                self.assertFalse(user_has_permission(user, product, Permissions.Finding_Delete))
            self.assertFalse(user_has_permission(user, self.b, Permissions.Product_View))
            self.assertFalse(user_has_permission(user, self.b, Permissions.Finding_Edit))

    def test_nested_inviter_cannot_select_a_subset_or_delegate_p2(self):
        for product in (self.a, self.b, self.c):
            with self.subTest(product=product), self.assertRaises(PermissionDenied):
                create_invited_user(requester=self.e, policy=self.policy, product=product, username="forged-child", email="forged-child@example.test")
        self.assertFalse(User.objects.filter(username="forged-child").exists())

    def test_nested_accounts_cannot_open_p2_product_engagement_or_finding(self):
        for user in (self.e, self.f):
            self.client.force_login(user)
            for url in (
                reverse("view_product", args=(self.b.pk,)), reverse("view_engagement", args=(self.other_engagement.pk,)),
                reverse("view_finding", args=(self.other_finding.pk,)), f"/api/v2/products/{self.b.pk}/",
                f"/api/v2/engagements/{self.other_engagement.pk}/", f"/api/v2/findings/{self.other_finding.pk}/",
            ):
                with self.subTest(user=user, url=url):
                    self.assertIn(self.client.get(url).status_code, (403, 404))

    def test_nested_accounts_can_view_both_inherited_product_scopes(self):
        for user in (self.e, self.f):
            self.client.force_login(user)
            for product, engagement, finding in ((self.a, self.engagement, self.finding), (self.c, self.third_engagement, self.third_finding)):
                for url in (
                    reverse("view_product", args=(product.pk,)), reverse("view_engagement", args=(engagement.pk,)),
                    reverse("view_finding", args=(finding.pk,)), f"/api/v2/products/{product.pk}/",
                    f"/api/v2/engagements/{engagement.pk}/", f"/api/v2/findings/{finding.pk}/",
                ):
                    with self.subTest(user=user, url=url):
                        self.assertEqual(self.client.get(url).status_code, 200)

    def test_nested_invitation_page_and_native_member_api_do_not_expose_p2(self):
        self.client.force_login(self.f)
        response = self.client.get(reverse("dojo_permission:manage_policy_members", args=(self.policy.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("product", response.context["invitation_form"].fields)
        self.assertEqual({row.product_id for row in response.context["product_memberships"]}, {self.a.pk, self.c.pk})
        for hidden in (self.foreign.username, self.foreign.email):
            self.assertNotContains(response, hidden)
        listed = self.client.get("/api/v2/dojo_group_members/").json()["results"]
        self.assertEqual({item["user"] for item in listed}, {self.user.pk, self.e.pk, self.f.pk, self.third.pk})

    def test_p2_member_still_cannot_open_p1_or_p3_after_nested_invitations(self):
        self.client.force_login(self.foreign)
        for product, finding in ((self.a, self.finding), (self.c, self.third_finding)):
            self.assertFalse(user_has_permission(self.foreign, product, Permissions.Product_View))
            self.assertIn(self.client.get(f"/api/v2/findings/{finding.pk}/").status_code, (403, 404))


class LegacyPolicyUpgradeTests(SingleGroupFixture, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_populated_old_product_group_keeps_identity_memberships_and_permissions(self):
        legacy = self.legacy_policy(self.a, name=f"Restricted Finding Editors - Product {self.a.pk}")
        member = Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.maintainer)
        before = (legacy.group_id, legacy.group.name, legacy.group.auth_group_id, member.pk, member.role_id)
        reconcile_managed_groups()
        legacy.refresh_from_db()
        member.refresh_from_db()
        self.assertEqual((legacy.group_id, legacy.group.name, legacy.group.auth_group_id, member.pk, member.role_id), before)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Finding_Delete))

    def test_empty_unmodified_generated_group_is_retired_on_post_migrate(self):
        legacy = self.legacy_policy(self.a)
        group_id, policy_id = legacy.group_id, legacy.pk
        call_command("migrate", "dojo_permission", interactive=False, verbosity=0)
        self.assertFalse(Dojo_Group.objects.filter(pk=group_id).exists())
        self.assertFalse(RestrictedGroupPolicy.objects.filter(pk=policy_id).exists())
        self.assertFalse(Product_Group.objects.filter(group_id=group_id).exists())
        self.assertTrue(RestrictedGroupPolicy.objects.filter(pk=self.policy.pk, is_default=True).exists())

    def test_customized_empty_group_is_not_retired(self):
        legacy = self.legacy_policy(self.a, description="Operator-managed custom description")
        reconcile_managed_groups()
        self.assertTrue(Dojo_Group.objects.filter(pk=legacy.group_id, description="Operator-managed custom description").exists())
        self.assertTrue(Product_Group.objects.filter(group=legacy.group, product=self.a, role=self.writer).exists())

    def test_operator_created_policy_is_not_retired_even_when_generated_labels_match(self):
        legacy = self.legacy_policy(self.a, created_by=self.user)
        reconcile_managed_groups()
        self.assertTrue(RestrictedGroupPolicy.objects.filter(pk=legacy.pk, created_by=self.user).exists())

    def test_group_linked_as_defectdojo_default_is_not_retired(self):
        legacy = self.legacy_policy(self.a)
        row = System_Settings.objects.get()
        row.default_group, row.default_group_role = legacy.group, self.reader
        row.save()
        reconcile_managed_groups()
        row.refresh_from_db()
        self.assertEqual(row.default_group_id, legacy.group_id)
        self.assertTrue(RestrictedGroupPolicy.objects.filter(pk=legacy.pk).exists())

    def test_shared_or_configured_legacy_access_grants_are_not_retired(self):
        legacy = self.legacy_policy(self.a)
        Product_Group.objects.create(group=legacy.group, product=self.b, role=self.maintainer)
        legacy.group.auth_group.permissions.add(Permission.objects.get(codename="view_user", content_type__app_label="auth"))
        reconcile_managed_groups()
        self.assertTrue(Dojo_Group.objects.filter(pk=legacy.group_id).exists())
        self.assertTrue(Product_Group.objects.filter(group=legacy.group, product=self.b, role=self.maintainer).exists())
        self.assertTrue(legacy.group.auth_group.permissions.exists())

    def test_product_creation_never_recreates_or_reactivates_legacy_groups(self):
        legacy = self.legacy_policy(self.a)
        legacy.is_active = False
        legacy.save(update_fields=("is_active",))
        self.a.description = "Product edited"
        self.a.save()
        self.product("Later P4")
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertFalse(RestrictedGroupPolicy.objects.filter(product__name="Later P4").exists())

    def test_compatibility_resolver_never_creates_renames_or_reactivates_a_product_group(self):
        from dojo_permission.provisioning import ProvisioningUnavailable, ensure_product_policy

        with self.assertRaises(ProvisioningUnavailable):
            ensure_product_policy(self.a)
        self.assertFalse(RestrictedGroupPolicy.objects.filter(product=self.a).exists())
        legacy = self.legacy_policy(self.a, name="Operator chosen legacy name")
        legacy.is_active = False
        legacy.save(update_fields=("is_active",))
        resolved = ensure_product_policy(self.a)
        self.assertEqual((resolved.pk, resolved.group_id, resolved.group.name, resolved.is_active), (legacy.pk, legacy.group_id, "Operator chosen legacy name", False))

    def test_legacy_source_invitation_requires_admin_enrollment_in_the_shared_group(self):
        legacy = self.legacy_policy(self.a)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.maintainer)
        self.membership.delete()
        with self.assertRaises(PermissionDenied):
            create_invited_user(requester=self.user, policy=legacy, username="legacy-child", email="legacy-child@example.test")
        self.assertEqual(self.client.get(reverse("dojo_permission:manage_policy_members", args=(legacy.pk,))).status_code, 403)
        self.assertFalse(User.objects.filter(username="legacy-child").exists())
        Dojo_Group_Member.objects.create(group=self.policy.group, user=self.user, role=self.writer)
        target = create_invited_user(requester=self.user, policy=self.policy, username="legacy-child", email="legacy-child@example.test")
        self.assertEqual(list(Dojo_Group_Member.objects.filter(user=target).values_list("group_id", "role_id")), [(self.policy.group_id, self.writer.pk)])
        self.assertEqual(list(Product_Member.objects.filter(user=target).values_list("product_id", "role_id")), [(self.a.pk, self.writer.pk)])
        self.assertFalse(user_has_permission(target, self.b, Permissions.Product_View))

    def test_scoped_legacy_owner_exemption_does_not_override_the_strict_shared_group(self):
        legacy = self.legacy_policy(self.a)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.owner)
        Product_Member.objects.create(product=self.b, user=self.user, role=self.writer)
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Engagement_Edit))
        self.membership.delete()
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertTrue(user_has_permission(self.user, self.b, Permissions.Engagement_Edit))

    def test_legacy_only_independent_product_maintainer_retains_its_native_exemption(self):
        legacy = self.legacy_policy(self.a)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.reader)
        self.membership.delete()
        Product_Member.objects.filter(product=self.a, user=self.user).update(role=self.maintainer)
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Engagement_Edit))
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Delete))

    def test_legacy_link_roles_and_global_writer_do_not_disable_overlay(self):
        legacy = self.legacy_policy(self.a, bound=False)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.reader)
        Product_Group.objects.filter(group=legacy.group).update(role=self.maintainer)
        self.assertTrue(policy_denies_permission(self.user, self.a, Permissions.Finding_Delete))
        Global_Role.objects.create(group=legacy.group, role=self.writer)
        self.assertFalse(user_has_permission(self.user, self.b, Permissions.Engagement_Edit))

    def test_legacy_product_maintainer_cannot_modify_group_assignments_or_add_privileged_members(self):
        legacy = self.legacy_policy(self.a)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.maintainer)
        assignment = Product_Group.objects.get(group=legacy.group)
        assignment.role = self.maintainer
        assignment.save(update_fields=("role",))
        for method in ("put", "delete"):
            response = getattr(self.client, method)(f"/api/v2/product_groups/{assignment.pk}/", data='{"role":4}', content_type="application/json")
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/v2/product_members/", {"product": self.a.pk, "user": self.foreign.pk, "role": self.maintainer.pk})
        self.assertEqual(response.status_code, 403)
        assignment.refresh_from_db()
        self.assertEqual(assignment.role_id, self.maintainer.pk)

    def test_unsafe_shared_legacy_group_cannot_delegate(self):
        legacy = self.legacy_policy(self.a)
        Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.reader)
        Product_Group.objects.create(group=legacy.group, product=self.b, role=self.writer)
        with self.assertRaises(PermissionDenied):
            create_invited_user(requester=self.user, policy=legacy, username="unsafe-child", email="unsafe-child@example.test")
        self.assertFalse(User.objects.filter(username="unsafe-child").exists())

    def test_existing_owner_rename_is_preserved_and_old_generated_default_label_can_upgrade(self):
        self.policy.group.name = "Operator chosen shared name"
        self.policy.group.save(update_fields=("name",))
        self.assertEqual(ensure_default_managed_group().group.name, "Operator chosen shared name")
        self.policy.group.name = "Restricted Finding Editors"
        self.policy.group.save(update_fields=("name",))
        marker = ensure_default_managed_group()
        self.assertEqual((marker.group_id, marker.group.name), (self.policy.group_id, "Dev Team group"))

    def test_default_rename_collision_does_not_adopt_an_unrelated_group(self):
        self.policy.group.name = "Restricted Finding Editors"
        self.policy.group.save(update_fields=("name",))
        unrelated = Dojo_Group.objects.create(name="Dev Team group")
        with self.assertRaises(ManagedGroupCollision):
            ensure_default_managed_group()
        self.assertFalse(RestrictedGroupPolicy.objects.filter(group=unrelated).exists())

    def test_product_policy_foreign_key_keeps_old_image_deletion_compatible(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL deployment constraint")
        with connection.cursor() as cursor:
            cursor.execute("SELECT confdeltype FROM pg_constraint WHERE conrelid = %s::regclass AND confrelid = %s::regclass", [RestrictedGroupPolicy._meta.db_table, Product._meta.db_table])
            self.assertEqual([row[0] for row in cursor.fetchall()], ["c"])
