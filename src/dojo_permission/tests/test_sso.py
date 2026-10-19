"""Identity-provider cleanup must not remove local restriction enrollment."""

from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from social_core.backends.azuread_tenant import AzureADTenantOAuth2
from social_django.models import UserSocialAuth

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions
from dojo.models import Dojo_Group, Dojo_Group_Member, Product_Member
from dojo.sso import pipeline, remote_user
from dojo_permission.sso import install_sso_overlay
from dojo_permission.tests.fixtures import SingleGroupFixture

User = get_user_model()


class SsoRestrictionEnrollmentTests(SingleGroupFixture, TestCase):
    def setUp(self):
        self.build_fixture()
        install_sso_overlay()

    def unrelated_group(self, name="Old identity provider group", provider=None):
        group = Dojo_Group.objects.create(name=name, social_provider=provider)
        membership = Dojo_Group_Member.objects.create(group=group, user=self.user, role=self.maintainer)
        return group, membership

    def test_native_cleanup_preserves_shared_membership_identity_and_exact_role(self):
        for role in (self.reader, self.writer, self.maintainer, self.owner):
            self.membership.role = role
            self.membership.save(update_fields=("role",))
            _, unrelated = self.unrelated_group(name=f"Old identity group {role.pk}")
            pipeline.cleanup_old_groups_for_user(self.user, [])
            self.membership.refresh_from_db()
            self.assertEqual(self.membership.role_id, role.pk)
            self.assertFalse(Dojo_Group_Member.objects.filter(pk=unrelated.pk).exists())
        self.assertEqual(Product_Member.objects.get(user=self.user, product=self.a).role_id, self.writer.pk)

    def test_native_cleanup_keeps_named_external_groups_without_mutating_input(self):
        kept_group, kept = self.unrelated_group(name="Current identity provider group", provider=Dojo_Group.AZURE)
        _, removed = self.unrelated_group()
        identity_group_names = [kept_group.name]
        pipeline.cleanup_old_groups_for_user(self.user, identity_group_names)
        self.assertEqual(identity_group_names, [kept_group.name])
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=self.membership.pk).exists())
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=kept.pk, role=self.maintainer).exists())
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=removed.pk).exists())

    def test_no_membership_is_created_when_the_user_was_not_enrolled(self):
        self.membership.delete()
        pipeline.cleanup_old_groups_for_user(self.user, [])
        self.assertFalse(Dojo_Group_Member.objects.filter(user=self.user).exists())
        self.assertEqual(Product_Member.objects.filter(user=self.user).count(), 1)

    def test_inactive_shared_policy_uses_native_cleanup(self):
        self.policy.is_active = False
        self.policy.save(update_fields=("is_active",))
        pipeline.cleanup_old_groups_for_user(self.user, [])
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=self.membership.pk).exists())

    def test_legacy_and_unrelated_memberships_still_follow_native_cleanup(self):
        legacy = self.legacy_policy(self.a)
        old_member = Dojo_Group_Member.objects.create(group=legacy.group, user=self.user, role=self.reader)
        pipeline.cleanup_old_groups_for_user(self.user, [])
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=self.membership.pk).exists())
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=old_member.pk).exists())

    def test_preserving_shared_enrollment_does_not_create_product_access(self):
        Product_Member.objects.filter(user=self.user).delete()
        pipeline.cleanup_old_groups_for_user(self.user, [])
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=self.membership.pk).exists())
        self.assertFalse(Product_Member.objects.filter(user=self.user).exists())
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Product_View))

    def test_unrestricted_users_keep_native_cleanup(self):
        outsider = User.objects.create_user(username="unrestricted-sso", email="unrestricted-sso@example.test")
        group = Dojo_Group.objects.create(name="Unrestricted external group")
        member = Dojo_Group_Member.objects.create(group=group, user=outsider, role=self.maintainer)
        pipeline.cleanup_old_groups_for_user(outsider, [])
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=member.pk).exists())
        self.assertFalse(Dojo_Group_Member.objects.filter(user=outsider, group=self.policy.group).exists())

    @override_settings(
        AZUREAD_TENANT_OAUTH2_ENABLED=True,
        AZUREAD_TENANT_OAUTH2_GET_GROUPS=True,
        AZUREAD_TENANT_OAUTH2_GROUPS_FILTER="",
        AZUREAD_TENANT_OAUTH2_CLEANUP_GROUPS=True,
    )
    def test_azure_sso_group_sync_keeps_restrictions_and_removes_unrelated_groups(self):
        group = Dojo_Group.objects.create(name="Current Azure team", social_provider=Dojo_Group.AZURE)
        _, removed = self.unrelated_group()
        UserSocialAuth.objects.create(user=self.user, provider="azuread-tenant-oauth2", uid="test-azure-member", extra_data={"access_token": ""})
        pipeline.update_azure_groups(
            Mock(spec=AzureADTenantOAuth2), "test-azure-member", user=self.user,
            response={"groups": [group.name]},
        )
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=self.membership.pk, role=self.reader).exists())
        self.assertTrue(Dojo_Group_Member.objects.filter(user=self.user, group=group, role=self.maintainer).exists())
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=removed.pk).exists())
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Finding_Delete))

    @override_settings(
        AUTH_REMOTEUSER_EMAIL_HEADER="", AUTH_REMOTEUSER_FIRSTNAME_HEADER="",
        AUTH_REMOTEUSER_LASTNAME_HEADER="", AUTH_REMOTEUSER_GROUPS_HEADER="HTTP_TEST_GROUPS",
        AUTH_REMOTEUSER_GROUPS_CLEANUP=True,
    )
    def test_remote_user_backend_imported_alias_preserves_shared_restrictions(self):
        group = Dojo_Group.objects.create(name="Current remote team", social_provider=Dojo_Group.REMOTE)
        _, removed = self.unrelated_group()
        request = RequestFactory().get("/", HTTP_TEST_GROUPS=group.name)
        result = remote_user.RemoteUserBackend().configure_user(request, self.user, created=False)
        self.assertEqual(result.pk, self.user.pk)
        self.assertTrue(Dojo_Group_Member.objects.filter(pk=self.membership.pk, role=self.reader).exists())
        self.assertTrue(Dojo_Group_Member.objects.filter(user=self.user, group=group, role=self.maintainer).exists())
        self.assertFalse(Dojo_Group_Member.objects.filter(pk=removed.pk).exists())
        self.assertTrue(user_has_permission(self.user, self.a, Permissions.Finding_Edit))
        self.assertFalse(user_has_permission(self.user, self.a, Permissions.Finding_Delete))

    def test_installation_is_idempotent_and_updates_the_remote_user_alias(self):
        original = pipeline.cleanup_old_groups_for_user
        install_sso_overlay()
        self.assertIs(pipeline.cleanup_old_groups_for_user, original)
        self.assertIs(remote_user.cleanup_old_groups_for_user, original)
