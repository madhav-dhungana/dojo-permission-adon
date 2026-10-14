"""Additive settings integration for an existing DefectDojo deployment."""

APP_CONFIG = "dojo_permission.apps.DojoPermissionConfig"
MIDDLEWARE = "dojo_permission.middleware.RestrictedActionMiddleware"
ROOT_URLCONF = "dojo_permission.root_urls"


def configure(settings_namespace: dict) -> None:
    installed_apps = list(settings_namespace.get("INSTALLED_APPS", ()))
    if APP_CONFIG not in installed_apps and "dojo_permission" not in installed_apps:
        installed_apps.append(APP_CONFIG)
    settings_namespace["INSTALLED_APPS"] = installed_apps

    middleware = list(settings_namespace.get("MIDDLEWARE", ()))
    if MIDDLEWARE not in middleware:
        middleware.append(MIDDLEWARE)
    settings_namespace["MIDDLEWARE"] = middleware

    parent_urlconf = settings_namespace.get("ROOT_URLCONF", "dojo.urls")
    if parent_urlconf != ROOT_URLCONF:
        settings_namespace["DOJO_PERMISSION_PARENT_URLCONF"] = parent_urlconf
    settings_namespace["ROOT_URLCONF"] = ROOT_URLCONF
