"""Keep local restriction enrollment when an identity provider syncs groups."""

from functools import wraps

from dojo.models import Dojo_Group_Member


def install_sso_overlay() -> None:
    """Preserve only existing active shared-policy memberships during cleanup.

    DefectDojo's Azure and remote-user cleanup removes every group not named by
    the identity provider, including locally managed groups. The shared policy
    is local authorization state, so include its existing membership in that
    cleanup's keep-list. All other memberships still use the native behavior.
    """
    from dojo.sso import pipeline
    from dojo_permission.privacy import replace_imported_references

    original = pipeline.cleanup_old_groups_for_user
    if getattr(original, "_dojo_permission_sso_cleanup", False):
        return

    @wraps(original)
    def cleanup_preserving_shared_policy(user, group_names):
        retained_names = list(group_names)
        retained_names.extend(
            Dojo_Group_Member.objects.filter(
                user=user,
                group__dojo_permission_policy__is_default=True,
                group__dojo_permission_policy__is_active=True,
            ).values_list("group__name", flat=True)
        )
        return original(user, retained_names)

    cleanup_preserving_shared_policy._dojo_permission_sso_cleanup = True
    # RemoteUserBackend imports this function directly. Replace any aliases
    # already loaded; later imports will obtain the patched pipeline function.
    replace_imported_references(original, cleanup_preserving_shared_policy)
    pipeline.cleanup_old_groups_for_user = cleanup_preserving_shared_policy
