"""Configuration compatibility tests for documented TRACE settings."""

from app.config import Settings


def test_debug_accepts_documented_boolean_values():
    assert Settings(DEBUG="true").DEBUG is True
    assert Settings(DEBUG="false").DEBUG is False


def test_legacy_release_marker_disables_debug_without_blocking_startup():
    assert Settings(DEBUG="release").DEBUG is False
