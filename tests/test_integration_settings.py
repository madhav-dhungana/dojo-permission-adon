from dojo_permission.integration import (
    APP_CONFIG,
    MIDDLEWARE,
    ROOT_URLCONF,
    TEMPLATE_OVERRIDE_DIR,
    configure,
)


def test_configure_is_additive_and_idempotent():
    namespace = {
        "INSTALLED_APPS": ["custom.app"],
        "MIDDLEWARE": ["custom.middleware"],
        "ROOT_URLCONF": "custom.urls",
    }

    configure(namespace)
    configure(namespace)

    assert namespace["INSTALLED_APPS"] == ["custom.app", APP_CONFIG]
    assert namespace["MIDDLEWARE"] == ["custom.middleware", MIDDLEWARE]
    assert namespace["DOJO_PERMISSION_PARENT_URLCONF"] == "custom.urls"
    assert namespace["ROOT_URLCONF"] == ROOT_URLCONF


def test_configure_prepends_template_wrapper_once_and_preserves_custom_dirs():
    namespace = {
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": ["/custom/templates"],
            },
        ],
    }

    configure(namespace)
    configure(namespace)

    directories = namespace["TEMPLATES"][0]["DIRS"]
    assert directories == [TEMPLATE_OVERRIDE_DIR, "/custom/templates"]
