from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from dojo.authorization.roles_permissions import Permissions
from dojo.models import Product
from dojo.product.queries import get_authorized_products

from dojo_permission.forms import NewUserInvitationForm
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.services import (
    InvitationEmailError,
    create_invited_user,
    policy_product,
    requester_policy_membership,
    send_password_invitation,
)


@transaction.non_atomic_requests
@login_required
def manage_policy_members(request, policy_id):
    policy = get_object_or_404(
        RestrictedGroupPolicy.objects.select_related(
            "group",
            "group__auth_group",
            "product",
        ),
        pk=policy_id,
        is_active=True,
    )
    requester_policy_membership(request.user, policy)
    fixed_product = policy_product(policy)
    product_queryset = (
        Product.objects.none()
        if fixed_product is not None
        else get_authorized_products(Permissions.Product_View, user=request.user)
    )

    form_kwargs = {
        "prefix": "invite",
        "product_queryset": product_queryset,
        "fixed_product": fixed_product,
    }
    invitation_form = NewUserInvitationForm(**form_kwargs)

    if request.method == "POST" and request.POST.get("action") != "invite_new":
        raise PermissionDenied

    if request.method == "POST" and request.POST.get("action") == "invite_new":
        invitation_form = NewUserInvitationForm(request.POST, **form_kwargs)
        if invitation_form.is_valid():
            try:
                user = create_invited_user(
                    requester=request.user,
                    policy=policy,
                    product=invitation_form.cleaned_data["product"],
                    username=invitation_form.cleaned_data["username"],
                    email=invitation_form.cleaned_data["email"],
                    first_name=invitation_form.cleaned_data["first_name"],
                    last_name=invitation_form.cleaned_data["last_name"],
                )
            except ValidationError as exc:
                invitation_form.add_error(None, exc)
            else:
                if (
                    invitation_form.cleaned_data["authentication_method"]
                    == NewUserInvitationForm.EMAIL
                ):
                    try:
                        send_password_invitation(user=user, request=request)
                    except InvitationEmailError:
                        messages.warning(
                            request,
                            "The account and restricted product membership were created, "
                            "but the setup email could not be sent.",
                        )
                    else:
                        messages.success(
                            request,
                            "The account was created and a password setup email was sent.",
                        )
                else:
                    messages.success(
                        request,
                        "The account was created for single sign-on.",
                    )
                return redirect(
                    "dojo_permission:manage_policy_members",
                    policy_id=policy.id,
                )

    return render(
        request,
        "dojo_permission/manage_policy_members.html",
        {
            "policy": policy,
            "fixed_product": fixed_product,
            "invitation_form": invitation_form,
        },
    )
