"""Regression coverage for native query helpers with independent access paths."""

from crum import impersonate
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions
from dojo.cred.queries import get_authorized_cred_mappings
from dojo.jira.models import JIRA_Issue, JIRA_Project
from dojo.jira.queries import get_authorized_jira_issues, get_authorized_jira_projects
from dojo.location.models import (
    Location,
    LocationFindingReference,
    LocationProductReference,
)
from dojo.location.queries import (
    get_authorized_location_finding_reference,
    get_authorized_location_product_reference,
    get_authorized_locations,
)
from dojo.models import (
    Cred_Mapping,
    Cred_User,
    Development_Environment,
    DojoMeta,
    Finding_Template,
    Global_Role,
    Product_Member,
)
from dojo.product.queries import get_authorized_dojo_meta
from dojo.product_type.queries import get_authorized_product_types
from dojo.test.queries import get_authorized_tests
from dojo.user import queries as user_queries
from dojo.url.ui import views as location_views
from dojo_permission.middleware import RestrictedActionMiddleware
from dojo_permission.privacy import install_privacy_overlay
from dojo_permission.tests.fixtures import SingleGroupFixture


class NativeProductScopeTests(SingleGroupFixture, TestCase):
    def setUp(self):
        install_privacy_overlay()
        self.build_fixture()
        # Exercise the overlay's upper bound: native DefectDojo queries would
        # otherwise return records from every product for this global role.
        Global_Role.objects.update_or_create(
            user=self.user,
            defaults={"role": self.writer},
        )

    def location(self, value, *, product=None, finding=None):
        location = Location.objects.create(
            location_type="host",
            location_value=value,
        )
        if product is not None:
            LocationProductReference.objects.create(
                location=location,
                product=product,
            )
        if finding is not None:
            LocationFindingReference.objects.create(
                location=location,
                finding=finding,
            )
        return location

    def test_metadata_query_intersects_every_parent_path_with_direct_products(self):
        assigned_location = self.location("assigned.example", product=self.a)
        hidden_location = self.location("hidden.example", product=self.b)
        allowed = {
            DojoMeta.objects.create(name="assigned-product", value="a", product=self.a).pk,
            DojoMeta.objects.create(name="assigned-finding", value="a", finding=self.finding).pk,
            DojoMeta.objects.create(name="assigned-location", value="a", location=assigned_location).pk,
        }
        DojoMeta.objects.create(name="hidden-product", value="b", product=self.b)
        DojoMeta.objects.create(name="hidden-finding", value="b", finding=self.other_finding)
        DojoMeta.objects.create(name="hidden-location", value="b", location=hidden_location)

        with impersonate(self.user):
            visible = set(
                get_authorized_dojo_meta(Permissions.Product_View).values_list(
                    "pk", flat=True,
                ),
            )
        self.assertEqual(visible, allowed)

    def test_jira_queries_use_direct_products_and_explicit_user(self):
        assigned_project = JIRA_Project.objects.create(
            project_key="A", product=self.a,
        )
        JIRA_Project.objects.create(project_key="B", product=self.b)
        assigned_issue = JIRA_Issue.objects.create(
            jira_id="assigned", jira_key="A-1", engagement=self.engagement,
        )
        JIRA_Issue.objects.create(
            jira_id="hidden", jira_key="B-1", engagement=self.other_engagement,
        )

        # A different current user proves that the explicit user at position 1
        # remains authoritative for helpers that support it.
        with impersonate(self.foreign):
            projects = set(
                get_authorized_jira_projects(
                    Permissions.Product_View,
                    self.user,
                ).values_list("pk", flat=True),
            )
        with impersonate(self.user):
            issues = set(
                get_authorized_jira_issues(Permissions.Product_View).values_list(
                    "pk", flat=True,
                ),
            )
        self.assertEqual(projects, {assigned_project.pk})
        self.assertEqual(issues, {assigned_issue.pk})

    def test_location_queries_use_all_product_paths_and_explicit_user(self):
        assigned_product_location = self.location("assigned-product.example", product=self.a)
        hidden_product_location = self.location("hidden-product.example", product=self.b)
        assigned_finding_location = self.location("assigned-finding.example", finding=self.finding)
        hidden_finding_location = self.location("hidden-finding.example", finding=self.other_finding)

        # A different current user proves that the explicit user at position 2
        # is used instead of CRUM for the native location signatures.
        with impersonate(self.foreign):
            locations = set(
                get_authorized_locations(
                    Permissions.Location_View,
                    None,
                    self.user,
                ).values_list("pk", flat=True),
            )
            product_refs = set(
                get_authorized_location_product_reference(
                    Permissions.Location_View,
                    None,
                    self.user,
                ).values_list("location_id", flat=True),
            )
            finding_refs = set(
                get_authorized_location_finding_reference(
                    Permissions.Location_View,
                    None,
                    self.user,
                ).values_list("location_id", flat=True),
            )

        self.assertEqual(
            locations,
            {assigned_product_location.pk, assigned_finding_location.pk},
        )
        self.assertEqual(product_refs, {assigned_product_location.pk})
        self.assertEqual(finding_refs, {assigned_finding_location.pk})
        self.assertNotIn(hidden_product_location.pk, locations)
        self.assertNotIn(hidden_finding_location.pk, locations)

    def test_credential_query_preserves_every_assigned_parent_path(self):
        environment = Development_Environment.objects.create(name="Scope test")
        credential = Cred_User.objects.create(
            name="Scope credential",
            username="scope-user",
            role="test",
            url="https://credentials.example.test",
            environment=environment,
        )
        assigned = {
            Cred_Mapping.objects.create(
                cred_id=credential,
                engagement=self.engagement,
            ).pk,
            Cred_Mapping.objects.create(
                cred_id=credential,
                test=self.test,
            ).pk,
            Cred_Mapping.objects.create(
                cred_id=credential,
                finding=self.finding,
            ).pk,
        }
        Cred_Mapping.objects.create(
            cred_id=credential,
            engagement=self.other_engagement,
        )

        with impersonate(self.user):
            visible = set(
                get_authorized_cred_mappings(
                    Permissions.Credential_View,
                ).values_list("pk", flat=True),
            )
        self.assertEqual(visible, assigned)

    def test_test_query_retains_its_explicit_product_context(self):
        Product_Member.objects.create(
            product=self.c,
            user=self.user,
            role=self.writer,
        )
        with impersonate(self.user):
            visible = set(
                get_authorized_tests(
                    Permissions.Test_View,
                    product=self.a,
                ).values_list("pk", flat=True),
            )
        self.assertEqual(visible, {self.test.pk})

    def test_explicit_user_overrides_a_different_current_user_for_privacy(self):
        peer = self.account("same-product-peer", self.a)
        with impersonate(self.foreign):
            visible = set(
                user_queries.get_authorized_users(
                    Permissions.Product_View,
                    user=self.user,
                ).values_list("pk", flat=True),
            )
        self.assertEqual(visible, {self.user.pk, peer.pk})

    def test_shared_user_cannot_create_a_product_via_product_type_role(self):
        Global_Role.objects.filter(user=self.user).update(role=self.maintainer)
        self.assertFalse(
            user_has_permission(
                self.user,
                self.a.prod_type,
                Permissions.Product_Type_Add_Product,
            ),
        )

    def test_product_type_and_metrics_are_fail_closed_without_blocking_home(self):
        # An unassigned product sharing the assigned product's type must not
        # contribute names, counts, or aggregate finding data.
        self.b.prod_type = self.a.prod_type
        self.b.save(update_fields=("prod_type",))
        Global_Role.objects.filter(user=self.user).update(role=self.maintainer)

        with impersonate(self.user):
            self.assertFalse(
                get_authorized_product_types(
                    Permissions.Product_Type_View,
                ).exists(),
            )
        self.assertFalse(
            user_has_permission(
                self.user,
                self.a.prod_type,
                Permissions.Product_Type_View,
            ),
        )

        params = {
            "month": timezone.now().month,
            "year": timezone.now().year,
            "product_type": self.a.prod_type_id,
        }
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("product_type")).status_code, 403)
        self.assertEqual(self.client.get(reverse("simple_metrics")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("product_type_counts"), params).status_code,
            403,
        )
        product_types = self.client.get("/api/v2/product_types/")
        self.assertEqual(product_types.status_code, 200)
        self.assertEqual(product_types.json()["results"], [])
        self.assertEqual(
            self.client.post("/api/v2/product_types/", {"name": "Denied type"}).status_code,
            403,
        )
        self.assertEqual(self.client.get(reverse("home"), follow=True).status_code, 200)

        admin = get_user_model().objects.create_superuser(
            username="native-scope-admin",
            email="native-scope-admin@example.test",
        )
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("product_type")).status_code, 200)
        self.assertEqual(self.client.get(reverse("simple_metrics")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("product_type_counts"), params).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/v2/product_types/").status_code, 200)

    def test_global_finding_templates_are_denied_for_assigned_and_unassigned_findings(self):
        Finding_Template.objects.create(
            title="Private global template",
            severity="High",
            description="Must not be visible to a shared restricted member.",
        )
        self.user.is_staff = True
        self.user.save(update_fields=("is_staff",))
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="dojo",
            content_type__model="finding_template",
        ))
        self.client.force_login(self.user)

        for finding in (self.finding, self.other_finding):
            with self.subTest(finding=finding.pk):
                self.assertEqual(
                    self.client.get(reverse("mktemplate", args=(finding.pk,))).status_code,
                    403,
                )
        for url in (
            reverse("templates"),
            reverse("export_template"),
            "/api/v2/finding_templates/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        admin = get_user_model().objects.create_superuser(
            username="native-template-admin",
            email="native-template-admin@example.test",
        )
        self.client.force_login(admin)
        self.assertEqual(
            self.client.get(reverse("mktemplate", args=(self.finding.pk,))).status_code,
            302,
        )
        self.assertEqual(self.client.get(reverse("templates")).status_code, 200)
        self.assertEqual(self.client.get(reverse("export_template")).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/finding_templates/").status_code, 200)

    def test_native_finding_id_routes_reject_cross_product_objects(self):
        self.client.force_login(self.user)
        request = RequestFactory().get(
            f"/endpoints/finding/{self.other_finding.pk}/add",
        )
        request.user = self.user
        middleware = RestrictedActionMiddleware(lambda _request: HttpResponse())
        with self.assertRaises(PermissionDenied):
            middleware.process_view(
                request,
                location_views.add_endpoint_to_finding,
                (),
                {"finding_id": str(self.other_finding.pk)},
            )
        self.assertEqual(
            self.client.get(
                reverse(
                    "add_risk_acceptance",
                    args=(self.engagement.pk, self.other_finding.pk),
                ),
            ).status_code,
            403,
        )
