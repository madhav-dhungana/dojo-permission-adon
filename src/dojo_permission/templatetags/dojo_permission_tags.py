from django import template

from dojo_permission.services import invitation_policies_for_user

register = template.Library()


@register.simple_tag
def dojo_permission_can_invite(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(invitation_policies_for_user(user))
