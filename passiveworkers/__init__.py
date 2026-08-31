"""Passive Workers — the Council: mutual-aid collective-intelligence MVP."""


def get_version() -> str:
    """The installed package version (single source of truth = pyproject), or 'dev' from a source
    checkout. Used to stamp the served web UIs without hardcoding a version in three places."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("passiveworkers")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"
