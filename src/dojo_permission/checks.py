from django.core.checks import Error, register

from dojo_permission.compat import (
    SUPPORTED_DEFECTDOJO_VERSION,
    is_supported_defectdojo_version,
)


@register()
def check_defectdojo_version(app_configs, **kwargs):
    import dojo

    installed_version = getattr(dojo, "__version__", None)
    if is_supported_defectdojo_version(installed_version):
        return []

    return [
        Error(
            "Unsupported DefectDojo version.",
            hint=(
                f"Install this library only with DefectDojo "
                f"{SUPPORTED_DEFECTDOJO_VERSION}; detected "
                f"{installed_version or 'unknown'}."
            ),
            id="dojo_permission.E001",
        ),
    ]
