from django.apps import AppConfig


class DojoPermissionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dojo_permission"
    verbose_name = "Dojo Permission"

    def ready(self) -> None:
        from dojo_permission import checks  # noqa: F401
        from dojo_permission.admin import register_admin
        from dojo_permission.patching import install_authorization_overlay
        from dojo_permission.provisioning import register_provisioning_signals

        register_admin()
        install_authorization_overlay()
        register_provisioning_signals(self)
