from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from dojo.authorization.roles_permissions import Roles
from dojo.models import (
    Dojo_Group,
    Dojo_Group_Member,
    Global_Role,
    Product,
    Product_Group,
    Product_Type,
    Product_Type_Group,
    Role,
    UserContactInfo,
)

from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.policy import RESTRICTED_MEMBER_ROLES

User = get_user_model()


@dataclass(frozen=True)
class MembershipResult:
    membership: Dojo_Group_Member
    created: bool


def writer_role() -> Role:
    return Role.objects.get(pk=Roles.Writer)


def requester_policy_membership(requester, policy: RestrictedGroupPolicy) -> Dojo_Group_Member:
    if not policy.is_active:
        raise PermissionDenied
    if requester.is_superuser:
        raise PermissionDenied
    if Dojo_Group_Member.objects.filter(
        user=requester,
        group=policy.group,
        role_id=Roles.Owner,
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
    if membership is None:
        raise PermissionDenied
    return membership


# Backwards-compatible alias for deployments importing the original helper.
requester_writer_membership = requester_policy_membership


@transaction.atomic
def add_existing_user_to_policy(
    *,
    requester,
    target_user,
    policy: RestrictedGroupPolicy,
) -> MembershipResult:
    policy = RestrictedGroupPolicy.objects.select_for_update().select_related("group").get(pk=policy.pk)
    requester_membership = requester_policy_membership(requester, policy)
    if target_user.is_superuser:
        raise ValidationError("A superuser cannot be delegated through a restricted group.")

    membership, created = Dojo_Group_Member.objects.get_or_create(
        group=policy.group,
        user=target_user,
        defaults={"role": requester_membership.role},
    )
    return MembershipResult(membership=membership, created=created)


@transaction.atomic
def create_invited_user(
    *,
    requester,
    policy: RestrictedGroupPolicy,
    username: str,
    email: str,
    first_name: str = "",
    last_name: str = "",
):
    policy = RestrictedGroupPolicy.objects.select_for_update().select_related("group").get(pk=policy.pk)
    requester_membership = requester_policy_membership(requester, policy)
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError("A user with this username already exists.")
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("A user with this email already exists; add the existing account instead.")

    user = User(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=True,
        is_staff=False,
        is_superuser=False,
    )
    user.set_unusable_password()
    user.save()
    UserContactInfo.objects.get_or_create(user=user)
    Global_Role.objects.get_or_create(user=user, defaults={"role": None})
    Dojo_Group_Member.objects.get_or_create(
        group=policy.group,
        user=user,
        defaults={"role": requester_membership.role},
    )
    return user


def send_password_invitation(*, user, request) -> None:
    from dojo.user.views import DojoPasswordResetForm

    form = DojoPasswordResetForm({"email": user.email})
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    form.save(request=request)


@transaction.atomic
def provision_policy_group(
    *,
    name: str,
    description: str = "",
    created_by=None,
    products: tuple[Product, ...] = (),
    product_types: tuple[Product_Type, ...] = (),
    initial_member=None,
) -> RestrictedGroupPolicy:
    group, _ = Dojo_Group.objects.get_or_create(
        name=name,
        defaults={"description": description},
    )
    policy, _ = RestrictedGroupPolicy.objects.get_or_create(
        group=group,
        defaults={"created_by": created_by, "is_active": True},
    )
    if not policy.is_active:
        policy.is_active = True
        policy.save(update_fields=("is_active", "updated_at"))

    role = writer_role()
    for product in products:
        assignment, created = Product_Group.objects.get_or_create(
            product=product,
            group=group,
            defaults={"role": role},
        )
        if not created and assignment.role_id != role.id:
            raise ValidationError(
                f"Group {group.name} already has a non-Writer role on product {product.id}.",
            )

    for product_type in product_types:
        assignment, created = Product_Type_Group.objects.get_or_create(
            product_type=product_type,
            group=group,
            defaults={"role": role},
        )
        if not created and assignment.role_id != role.id:
            raise ValidationError(
                f"Group {group.name} already has a non-Writer role on product type {product_type.id}.",
            )

    if initial_member is not None:
        Dojo_Group_Member.objects.get_or_create(
            group=group,
            user=initial_member,
            defaults={"role": role},
        )
    return policy
