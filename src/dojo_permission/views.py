from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from dojo.models import Dojo_Group_Member

from dojo_permission.forms import ExistingUserForm, NewUserInvitationForm
from dojo_permission.models import RestrictedGroupPolicy
from dojo_permission.services import (
    add_existing_user_to_policy,
    create_invited_user,
    requester_policy_membership,
    send_password_invitation,
)


@login_required
def manage_policy_members(request, policy_id):
    policy = get_object_or_404(
        RestrictedGroupPolicy.objects.select_related("group"),
        pk=policy_id,
        is_active=True,
    )
    requester_policy_membership(request.user, policy)

    existing_form = ExistingUserForm(policy=policy, prefix="existing")
    invitation_form = NewUserInvitationForm(prefix="invite")

    if request.method == "POST" and request.POST.get("action") == "add_existing":
        existing_form = ExistingUserForm(request.POST, policy=policy, prefix="existing")
        if existing_form.is_valid():
            result = add_existing_user_to_policy(
                requester=request.user,
                target_user=existing_form.cleaned_data["user"],
                policy=policy,
            )
            if result.created:
                messages.success(request, "The existing user was added to the restricted group.")
            else:
                messages.info(request, "The user already belongs to the restricted group; no role was changed.")
            return redirect("dojo_permission:manage_policy_members", policy_id=policy.id)

    if request.method == "POST" and request.POST.get("action") == "invite_new":
        invitation_form = NewUserInvitationForm(request.POST, prefix="invite")
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
                if invitation_form.cleaned_data["authentication_method"] == NewUserInvitationForm.EMAIL:
                    try:
                        send_password_invitation(user=user, request=request)
                    except ValidationError as exc:
                        messages.warning(
                            request,
                            f"The account and group membership were created, but the email could not be sent: {exc}",
                        )
                    else:
                        messages.success(request, "The account was created and a password setup email was sent.")
                else:
                    messages.success(request, "The account was created for single sign-on.")
                return redirect("dojo_permission:manage_policy_members", policy_id=policy.id)
            except ValidationError as exc:
                invitation_form.add_error(None, exc)

    members = (
        Dojo_Group_Member.objects.filter(group=policy.group)
        .select_related("user", "role")
        .order_by("user__username")
    )
    return render(request, "dojo_permission/manage_policy_members.html", {
        "policy": policy,
        "members": members,
        "existing_form": existing_form,
        "invitation_form": invitation_form,
    })
