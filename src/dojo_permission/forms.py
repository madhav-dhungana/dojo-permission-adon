from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from crum import get_current_user
from dojo.forms import DojoGroupForm
from dojo.authorization.roles_permissions import Roles
from dojo.models import Dojo_Group_Member

from dojo_permission.models import RestrictedGroupPolicy

User = get_user_model()


class RestrictedDojoGroupForm(DojoGroupForm):
    apply_restrictions = forms.BooleanField(
        required=False,
        label="Do you want to apply delete/edit restrictions to this group?",
        help_text=(
            "Apply the restricted-group policy to Reader, Writer, and Maintainer "
            "members. Group Owners and superusers remain exempt."
        ),
    )

    def __init__(self, *args, **kwargs):
        self.policy_actor = kwargs.pop("policy_actor", None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["apply_restrictions"].initial = RestrictedGroupPolicy.objects.filter(
                group_id=self.instance.pk,
                is_active=True,
            ).exists()
            actor = self.policy_actor or get_current_user()
            actor_can_change_policy = actor is not None and (
                not actor.is_anonymous
                and (
                    actor.is_superuser
                    or Dojo_Group_Member.objects.filter(
                        group_id=self.instance.pk,
                        user=actor,
                        role_id=Roles.Owner,
                    ).exists()
                )
            )
            if not actor_can_change_policy:
                self.fields["apply_restrictions"].disabled = True
                self.fields["apply_restrictions"].help_text += (
                    " Only a group Owner or superuser can change this setting."
                )

    def save(self, commit=True):
        group = super().save(commit=commit)
        if not commit:
            return group

        apply_restrictions = self.cleaned_data.get("apply_restrictions", False)
        if not apply_restrictions:
            RestrictedGroupPolicy.objects.filter(group=group, is_active=True).update(is_active=False)
            return group

        actor = self.policy_actor or get_current_user()
        if actor is not None and (actor.is_anonymous or not actor.pk):
            actor = None
        policy, created = RestrictedGroupPolicy.objects.get_or_create(
            group=group,
            defaults={
                "created_by": actor,
                "is_active": True,
            },
        )
        if not created and not policy.is_active:
            policy.is_active = True
            policy.save(update_fields=("is_active", "updated_at"))
        return group


class ExistingUserForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.none())

    def __init__(self, *args, policy, **kwargs):
        super().__init__(*args, **kwargs)
        current_member_ids = Dojo_Group_Member.objects.filter(
            group=policy.group,
        ).values_list("user_id", flat=True)
        self.fields["user"].queryset = (
            User.objects.filter(is_active=True, is_superuser=False)
            .exclude(id__in=current_member_ids)
            .order_by("username")
        )


class NewUserInvitationForm(forms.Form):
    EMAIL = "email"
    SSO = "sso"
    AUTH_METHODS = (
        (EMAIL, "Email password setup"),
        (SSO, "Single sign-on"),
    )

    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    authentication_method = forms.ChoiceField(choices=AUTH_METHODS)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(Q(email__iexact=email)).exists():
            raise forms.ValidationError("This email belongs to an existing account; add that account instead.")
        return email
