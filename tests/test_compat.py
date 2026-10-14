from dojo_permission.compat import (
    SUPPORTED_DEFECTDOJO_VERSION,
    is_supported_defectdojo_version,
)


def test_accepts_supported_defectdojo_version():
    assert is_supported_defectdojo_version(SUPPORTED_DEFECTDOJO_VERSION)


def test_rejects_other_or_missing_versions():
    assert not is_supported_defectdojo_version("2.59.0")
    assert not is_supported_defectdojo_version(None)
