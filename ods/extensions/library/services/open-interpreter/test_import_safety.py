"""Test that open-interpreter server can be imported outside the container."""

from pathlib import Path
from unittest.mock import patch


def test_server_imports_without_app_data_mkdir():
    """The module-level code must not call mkdir on /app/data.

    server.py runs in a container where /app exists (the Dockerfile creates it),
    but outside the container (e.g., in CI tests) that path doesn't exist and
    mkdir would fail. Moving the mkdir call out of module scope lets the server
    be imported outside its container.
    """
    # If the server tries to mkdir /app/data, this patch catches it and raises.
    with patch.object(Path, "mkdir", side_effect=RuntimeError("mkdir called")):
        try:
            # Import should succeed even with mkdir blocked.
            spec = __import__("importlib.util").util.spec_from_file_location(
                "server", Path(__file__).parent / "server.py"
            )
            spec.loader.exec_module(__import__("importlib.util").util.module_from_spec(spec))
        except RuntimeError as e:
            if "mkdir called" in str(e):
                raise AssertionError(
                    "server.py still calls mkdir at module import time; "
                    "the Dockerfile's 'RUN mkdir' should be the only source"
                ) from e
            raise


if __name__ == "__main__":
    test_server_imports_without_app_data_mkdir()
    print("✓ server imports safely")
