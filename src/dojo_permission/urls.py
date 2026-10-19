from django.urls import path

from dojo_permission import views

app_name = "dojo_permission"

urlpatterns = [
    path(
        "accounts/create/",
        views.invitation_entry,
        name="invitation_entry",
    ),
    path(
        "groups/<int:policy_id>/members/",
        views.manage_policy_members,
        name="manage_policy_members",
    ),
]
