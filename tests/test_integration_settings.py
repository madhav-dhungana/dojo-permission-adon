from dojo_permission.integration import APP_CONFIG, MIDDLEWARE, ROOT_URLCONF, configure


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
