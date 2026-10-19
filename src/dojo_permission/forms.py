from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()

GENERIC_ACCOUNT_ERROR = "Unable to create this account."


class NewUserInvitationForm(forms.Form):
    EMAIL = "email"
    SSO = "sso"
    AUTH_METHODS = (
        (EMAIL, "Email password setup"),
        (SSO, "Single sign-on"),
    )

    username = forms.CharField(
        max_length=150,
        validators=User._meta.get_field("username").validators,
    )
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    authentication_method = forms.ChoiceField(choices=AUTH_METHODS)

    def clean_username(self):
        return self.cleaned_data["username"].strip()

    def clean_email(self):
        return self.cleaned_data["email"].strip()

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        email = cleaned_data.get("email")
        if username and email and User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=email),
        ).exists():
            raise forms.ValidationError(GENERIC_ACCOUNT_ERROR)
        return cleaned_data
