"""Shared pytest fixtures for the FoxESS integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield
