"""Side-effect-free app version metadata."""

APP_NAME = "ViriaRevive"
APP_VERSION = "2.3.0"
APP_VERSION_DISPLAY = f"v{APP_VERSION}"
APP_VERSION_QUAD = f"{APP_VERSION}.0"
APP_COMPANY = "Expired Soda"
APP_DESCRIPTION = "AI-assisted gameplay Shorts clipper and scheduler"


def version_tuple(value: str = APP_VERSION_QUAD) -> tuple[int, int, int, int]:
    parts = [int(part) for part in value.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


APP_VERSION_TUPLE = version_tuple()
