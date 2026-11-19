import os


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


WORKSPACE_MAX_BYTES = _int_env('WORKSPACE_MAX_BYTES', str(100 * 1024 * 1024))
WORKSPACE_SHELL_MAX_BYTES = _int_env('WORKSPACE_SHELL_MAX_BYTES', str(8 * 1024 * 1024))
