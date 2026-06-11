"""Global pytest configuration.

We disable the JWT authentication layer by default for the existing test
suite so that no test has to think about bearer tokens. Tests that need to
*verify* the auth layer itself re-enable it locally via monkeypatch (see
``test_auth_routes.py``).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``AUTH_ENABLED=False`` for every test unless explicitly overridden."""
    from backend.core import config as config_module

    monkeypatch.setattr(
        config_module.settings, "AUTH_ENABLED", False, raising=False
    )
