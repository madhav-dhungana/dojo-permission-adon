from django.urls import path

from dojo_permission import views

app_name = "dojo_permission"

urlpatterns = [
    path(
        "groups/<int:policy_id>/members/",
        views.manage_policy_members,
        name="manage_policy_members",
    ),
]
