from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from dojo_permission.forms import NewUserInvitationForm
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.services import (
    InvitationEmailError,
    create_invited_user,
    invitation_policies_for_user,
    invitable_product_memberships,
    requester_policy_membership,
    send_password_invitation,
)


@login_required
@require_GET
def invitation_entry(request):
    policies = invitation_policies_for_user(request.user)
    if not policies:
        raise PermissionDenied
    if len(policies) == 1:
        return redirect(
            "dojo_permission:manage_policy_members",
            policy_id=policies[0].pk,
        )

    raise PermissionDenied


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
    product_memberships = invitable_product_memberships(request.user, policy)
    form_kwargs = {"prefix": "invite"}
    invitation_form = NewUserInvitationForm(**form_kwargs)

    if request.method == "POST" and request.POST.get("action") != "invite_new":
        raise PermissionDenied

    allowed_fields = {
        "csrfmiddlewaretoken", "action", "invite-username", "invite-email",
        "invite-first_name", "invite-last_name", "invite-authentication_method",
    }
    if request.method == "POST" and set(request.POST).difference(allowed_fields):
        raise PermissionDenied

    if request.method == "POST" and request.POST.get("action") == "invite_new":
        invitation_form = NewUserInvitationForm(request.POST, **form_kwargs)
        if invitation_form.is_valid():
            try:
                user = create_invited_user(
                    requester=request.user,
                    policy=policy,
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
                            "The account and inherited product memberships were created, "
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
            "product_memberships": product_memberships,
            "invitation_form": invitation_form,
        },
    )
