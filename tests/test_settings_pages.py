"""Page taxonomy, live-knob metadata, and helper coverage for settings_store.

Covers the category-page data model: every field maps to a known sidebar page,
``to_schema`` emits ``page``/``live``/``pages``, and the live-knob helpers
(``live_keys``, ``runtime_overrides``, ``coerce_env_value``) behave as the
``/settings`` handlers rely on.
"""

from __future__ import annotations

import os

import pytest

from headroom import settings_store
from headroom.settings_store import PAGES, SETTINGS

_LIVE_KEYS = {
    "output_shaper",
    "verbosity_level",
    "effort_router",
    "mechanical_effort",
    "verbosity_autotune",
    "output_holdout",
    "intercept_read_min_chars",
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolate settings.json under a tmp workspace and clear ambient env knobs."""
    monkeypatch.setenv(settings_store.paths.HEADROOM_WORKSPACE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(settings_store.paths.HEADROOM_SETTINGS_PATH_ENV, raising=False)
    for field in SETTINGS:
        monkeypatch.delenv(field.env, raising=False)
    return tmp_path


class TestPageTaxonomy:
    def test_every_field_has_a_known_page(self):
        for field in SETTINGS:
            assert field.page in PAGES, f"{field.key} -> {field.page!r} not in PAGES"

    def test_schema_pages_populated_and_ordered(self, workspace):
        schema = settings_store.to_schema()
        populated = [p for p in PAGES if any(f.page == p for f in SETTINGS)]
        assert schema["pages"] == populated
        # The remap fills every page, so all nine appear in nav order.
        assert schema["pages"] == list(PAGES)

    def test_no_ccr_moved_to_ccr_and_caching(self):
        assert settings_store._BY_KEY["no_ccr"].page == "CCR & Caching"
        assert settings_store._BY_KEY["no_ccr_proactive_expansion"].page == "CCR & Caching"

    def test_schema_fields_carry_page_and_live(self, workspace):
        by_key = {f["key"]: f for f in settings_store.to_schema()["fields"]}
        assert by_key["output_shaper"]["page"] == "Output Shaping"
        assert by_key["output_shaper"]["live"] is True
        assert by_key["target_ratio"]["live"] is False


class TestLiveKnobs:
    def test_only_output_shaping_is_live(self):
        assert {f.key for f in SETTINGS if f.live} == _LIVE_KEYS
        assert all(f.page == "Output Shaping" for f in SETTINGS if f.live)

    def test_live_keys_helper_filters_non_live(self):
        assert settings_store.live_keys(["output_shaper", "target_ratio", "no_ccr"]) == [
            "output_shaper"
        ]

    def test_runtime_overrides_serializes_live_only(self):
        overrides = settings_store.runtime_overrides(
            ["output_shaper", "verbosity_level", "target_ratio"],
            {"output_shaper": True, "verbosity_level": 3, "target_ratio": 0.5},
        )
        assert overrides == {"HEADROOM_OUTPUT_SHAPER": "1", "HEADROOM_VERBOSITY_LEVEL": "3"}

    def test_runtime_overrides_skips_absent_keys(self):
        assert settings_store.runtime_overrides(["verbosity_level"], {}) == {}

    def test_coerce_env_value_roundtrips(self):
        assert settings_store.coerce_env_value("verbosity_level", "3") == 3
        assert settings_store.coerce_env_value("output_shaper", "on") is True
        assert settings_store.coerce_env_value("verbosity_level", "") is None
        assert settings_store.coerce_env_value("verbosity_level", None) is None
        assert settings_store.coerce_env_value("verbosity_level", "notanint") is None
        assert settings_store.coerce_env_value("nope", "1") is None


class TestObservabilityKnobs:
    @pytest.mark.parametrize(
        "key",
        [
            "otel_metrics_enabled",
            "otel_metrics_endpoint",
            "otel_service_name",
            "langfuse_enabled",
            "telemetry",
            "periodic_toin_stats",
        ],
    )
    def test_registered_on_observability_page(self, key):
        assert settings_store._BY_KEY[key].page == "Observability"

    def test_optional_bool_telemetry_roundtrip(self, workspace):
        settings_store.save({"telemetry": True})
        assert settings_store.load()["telemetry"] is True
        settings_store.apply_to_environ(settings_store.load())
        # "1" is one of the beacon's accepted on-values.
        assert os.environ["HEADROOM_TELEMETRY"] == "1"


class TestLivePersistence:
    def test_apply_to_environ_skips_live_knobs(self, workspace):
        settings_store.apply_to_environ({"output_shaper": True, "target_ratio": 0.5})
        # Non-live knobs seed the environment; live knobs do not (they use the
        # runtime_env override store, so they stay GUI-editable after a restart).
        assert os.environ["HEADROOM_TARGET_RATIO"] == "0.5"
        assert "HEADROOM_OUTPUT_SHAPER" not in os.environ

    def test_env_for(self):
        assert settings_store.env_for("output_shaper") == "HEADROOM_OUTPUT_SHAPER"
        assert settings_store.env_for("nope") is None
