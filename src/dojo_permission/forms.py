from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from dojo.models import Product

User = get_user_model()

GENERIC_ACCOUNT_ERROR = "Unable to create this account."


class NewUserInvitationForm(forms.Form):
    EMAIL = "email"
    SSO = "sso"
    AUTH_METHODS = (
        (EMAIL, "Email password setup"),
        (SSO, "Single sign-on"),
    )

    product = forms.ModelChoiceField(queryset=Product.objects.none())
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    authentication_method = forms.ChoiceField(choices=AUTH_METHODS)

    def __init__(
        self,
        *args,
        product_queryset=None,
        fixed_product=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if fixed_product is not None:
            self.fields["product"].queryset = Product.objects.filter(pk=fixed_product.pk)
            self.fields["product"].initial = fixed_product
            self.fields["product"].disabled = True
        else:
            self.fields["product"].queryset = (
                product_queryset
                if product_queryset is not None
                else Product.objects.none()
            )

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
