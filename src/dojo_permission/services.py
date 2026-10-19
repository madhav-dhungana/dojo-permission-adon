"""Narrow service boundary for managed restricted-group delegation."""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from dojo.authorization.authorization import user_has_permission
from dojo.authorization.roles_permissions import Permissions, Roles
from dojo.models import (
    Dojo_Group,
    Dojo_Group_Member,
    Global_Role,
    Product,
    Product_Group,
    Product_Type_Group,
    Role,
    System_Settings,
    UserContactInfo,
)
from dojo.user.views import DojoPasswordResetForm

from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.policy import RESTRICTED_MEMBER_ROLES
from dojo_permission.provisioning import (
    ManagedGroupCollision,
    ProvisioningUnavailable,
    ensure_product_policy,
    is_default_managed_policy,
)

User = get_user_model()

GENERIC_ACCOUNT_ERROR = "Unable to create this account."


class InvitationEmailError(RuntimeError):
    """The account exists, but its setup email could not be sent."""


@dataclass(frozen=True)
class MembershipResult:
    """Retained for callers that imported the original service contract."""

    membership: Dojo_Group_Member
    created: bool


def writer_role() -> Role:
    return Role.objects.get(pk=Roles.Writer)


def _validate_group_delegation_boundary(policy: RestrictedGroupPolicy) -> list[Product_Group]:
    """Fail closed if a policy group carries access outside one product."""

    group = policy.group
    if group.auth_group_id is None or group.auth_group.permissions.exists():
        raise PermissionDenied
    # DefectDojo creates a neutral Global_Role row when an administrator saves a
    # group. A null role grants nothing and is safe; a real role is not.
    if Global_Role.objects.filter(group=group, role__isnull=False).exists():
        raise PermissionDenied
    if Product_Type_Group.objects.filter(group=group).exists():
        raise PermissionDenied
    return list(Product_Group.objects.filter(group=group).select_related("product", "role"))


def policy_product(policy: RestrictedGroupPolicy) -> Product | None:
    """Resolve the one safe delegation product, or the access-free marker."""

    assignments = _validate_group_delegation_boundary(policy)
    if is_default_managed_policy(policy):
        if policy.product_id is not None or assignments:
            raise PermissionDenied
        return None

    if policy.product_id is not None:
        if (
            len(assignments) != 1
            or assignments[0].product_id != policy.product_id
            or assignments[0].role_id != Roles.Writer
        ):
            raise PermissionDenied
        return policy.product

    # A pre-0.2 policy can delegate only when its old links unambiguously name
    # one Writer product. Shared/product-type/global legacy scopes are rejected.
    if len(assignments) != 1 or assignments[0].role_id != Roles.Writer:
        raise PermissionDenied
    return assignments[0].product


def requester_policy_membership(
    requester,
    policy: RestrictedGroupPolicy,
) -> Dojo_Group_Member:
    if (
        not policy.is_active
        or requester is None
        or requester.is_anonymous
        or requester.is_superuser
    ):
        raise PermissionDenied
    if Dojo_Group_Member.objects.filter(
        user=requester,
        group=policy.group,
        role__is_owner=True,
    ).exists():
        raise PermissionDenied
    membership = (
        Dojo_Group_Member.objects.select_related("role", "group")
        .filter(
            user=requester,
            group=policy.group,
            role_id__in=RESTRICTED_MEMBER_ROLES,
        )
        .first()
    )
    if membership is None or membership.role.is_owner:
        raise PermissionDenied

    product = policy_product(policy)
    if product is not None and not user_has_permission(
        requester,
        product,
        Permissions.Product_View,
    ):
        raise PermissionDenied
    return membership


# Backwards-compatible alias for deployments importing the original helper.
requester_writer_membership = requester_policy_membership


def invitation_policies_for_user(user) -> tuple[RestrictedGroupPolicy, ...]:
    """Return only policy scopes through which this user can really invite."""

    if user is None or user.is_anonymous or user.is_superuser:
        return ()
    eligible_group_ids = Dojo_Group_Member.objects.filter(
        user=user,
        role_id__in=RESTRICTED_MEMBER_ROLES,
        group__dojo_permission_policy__is_active=True,
    ).values("group_id")
    policies = (
        RestrictedGroupPolicy.objects.filter(
            is_active=True,
            group_id__in=eligible_group_ids,
        )
        .select_related("group", "group__auth_group", "product")
        .order_by("-is_default", "product__name", "group__name")
    )

    available = []
    for policy in policies:
        try:
            requester_policy_membership(user, policy)
            product = policy_product(policy)
        except PermissionDenied:
            continue
        if product is None:
            from dojo.product.queries import get_authorized_products

            if not get_authorized_products(
                Permissions.Finding_Edit,
                user=user,
            ).exists():
                continue
        available.append(policy)
    return tuple(available)


def add_existing_user_to_policy(
    *,
    requester,
    target_user,
    policy: RestrictedGroupPolicy,
) -> MembershipResult:
    """Existing-account membership remains an administrator-only native action."""

    raise PermissionDenied


def _selected_target_policy(
    *,
    requester,
    source_policy: RestrictedGroupPolicy,
    selected_product: Product | None,
) -> RestrictedGroupPolicy:
    source_product = policy_product(source_policy)
    if source_product is not None:
        if selected_product is not None and selected_product.pk != source_product.pk:
            raise PermissionDenied
        return source_policy

    if selected_product is None or not user_has_permission(
        requester,
        selected_product,
        Permissions.Finding_Edit,
    ):
        raise PermissionDenied
    try:
        return ensure_product_policy(selected_product)
    except (ManagedGroupCollision, ProvisioningUnavailable) as exc:
        raise ValidationError(GENERIC_ACCOUNT_ERROR) from exc


def _remove_new_user_default_group(user) -> None:
    settings_row = System_Settings.objects.select_related("default_group").first()
    if settings_row is None or settings_row.default_group_id is None:
        return
    Dojo_Group_Member.objects.filter(
        user=user,
        group_id=settings_row.default_group_id,
    ).delete()


@transaction.atomic
def create_invited_user(
    *,
    requester,
    policy: RestrictedGroupPolicy,
    username: str,
    email: str,
    first_name: str = "",
    last_name: str = "",
    product: Product | None = None,
):
    source_policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .select_related("group", "group__auth_group", "product")
        .get(pk=policy.pk)
    )
    Dojo_Group.objects.select_for_update().get(pk=source_policy.group_id)
    requester_membership = requester_policy_membership(requester, source_policy)
    target_policy = _selected_target_policy(
        requester=requester,
        source_policy=source_policy,
        selected_product=product,
    )
    target_policy = (
        RestrictedGroupPolicy.objects.select_for_update(of=("self",))
        .select_related("group", "group__auth_group", "product")
        .get(pk=target_policy.pk)
    )
    if target_policy.group_id != source_policy.group_id:
        Dojo_Group.objects.select_for_update().get(pk=target_policy.group_id)
    if not target_policy.is_active or policy_product(target_policy) is None:
        raise PermissionDenied

    username = username.strip()
    email = email.strip()
    if User.objects.filter(username__iexact=username).exists() or User.objects.filter(
        email__iexact=email,
    ).exists():
        raise ValidationError(GENERIC_ACCOUNT_ERROR)

    try:
        user = User(
            username=username,
            email=email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        user.save()
        UserContactInfo.objects.get_or_create(user=user)
        # DefectDojo's user post-save receiver may add its configured default
        # group. Remove that membership only for this freshly created account.
        _remove_new_user_default_group(user)
        Dojo_Group_Member.objects.create(
            group=target_policy.group,
            user=user,
            role=requester_membership.role,
        )
    except IntegrityError as exc:
        raise ValidationError(GENERIC_ACCOUNT_ERROR) from exc
    return user


class InvitedUserPasswordResetForm(DojoPasswordResetForm):
    """Issue a normal setup token for one explicitly invited passwordless user."""

    def __init__(self, *args, invited_user, **kwargs):
        self.invited_user = invited_user
        super().__init__(*args, **kwargs)

    def get_users(self, email):
        user = self.invited_user
        if (
            user.is_active
            and not user.has_usable_password()
            and user.email
            and user.email.casefold() == email.casefold()
        ):
            yield user


def send_password_invitation(*, user, request) -> None:
    if not user.is_active or user.has_usable_password() or not user.email:
        raise InvitationEmailError
    form = InvitedUserPasswordResetForm(
        {"email": user.email},
        invited_user=user,
    )
    try:
        if not form.is_valid():
            raise InvitationEmailError
        form.save(request=request, use_https=request.is_secure())
    except InvitationEmailError:
        raise
    except Exception as exc:
        raise InvitationEmailError from exc
