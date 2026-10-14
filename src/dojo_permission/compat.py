SUPPORTED_DEFECTDOJO_VERSION = "2.58.3"


def is_supported_defectdojo_version(version: str | None) -> bool:
    return version == SUPPORTED_DEFECTDOJO_VERSION
