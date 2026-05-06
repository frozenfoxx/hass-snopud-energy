"""Test bootstrap.

Home Assistant is a heavyweight dep; rather than installing it just to
exercise our coordinator math, we register lightweight stub modules
under ``homeassistant.*`` so the coordinator can be imported and its
``_async_import_statistics`` method called with mocked I/O.

The stubs reflect the contracts we actually depend on:
  * ``StatisticData`` / ``StatisticMetaData`` / ``StatisticMeanType``
    from ``homeassistant.components.recorder.models``
  * ``async_add_external_statistics`` / ``get_last_statistics`` from
    ``homeassistant.components.recorder.statistics``
  * ``UnitOfEnergy.KILO_WATT_HOUR`` from ``homeassistant.const``
  * ``dt_util.start_of_local_day`` from ``homeassistant.util.dt``
  * ``DataUpdateCoordinator`` / ``UpdateFailed`` (no-op subclass base)
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# homeassistant package skeleton
# ---------------------------------------------------------------------------

ha = types.ModuleType("homeassistant")
ha_components = types.ModuleType("homeassistant.components")
ha_components_recorder = types.ModuleType("homeassistant.components.recorder")
ha_components_recorder_models = types.ModuleType(
    "homeassistant.components.recorder.models"
)
ha_components_recorder_statistics = types.ModuleType(
    "homeassistant.components.recorder.statistics"
)
ha_const = types.ModuleType("homeassistant.const")
ha_core = types.ModuleType("homeassistant.core")
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers_update = types.ModuleType("homeassistant.helpers.update_coordinator")
ha_util = types.ModuleType("homeassistant.util")
ha_util_dt = types.ModuleType("homeassistant.util.dt")
ha_config_entries = types.ModuleType("homeassistant.config_entries")


# --- recorder.models -------------------------------------------------------


class StatisticMeanType(Enum):
    """Stub mirroring the real enum."""

    NONE = "none"
    ARITHMETIC = "arithmetic"


@dataclass
class StatisticData:
    """Stub mirroring TypedDict fields we use."""

    start: datetime
    state: float | None = None
    sum: float | None = None


@dataclass
class StatisticMetaData:
    """Stub mirroring TypedDict fields we use."""

    has_mean: bool
    has_sum: bool
    mean_type: StatisticMeanType
    name: str
    source: str
    statistic_id: str
    unit_of_measurement: str | None


ha_components_recorder_models.StatisticData = StatisticData
ha_components_recorder_models.StatisticMetaData = StatisticMetaData
ha_components_recorder_models.StatisticMeanType = StatisticMeanType


# --- recorder.statistics ---------------------------------------------------

# Test code reads these to assert what was imported.
imported_metadata: list[StatisticMetaData] = []
imported_data: list[list[StatisticData]] = []
# Pre-populated by tests to simulate prior imports.
last_statistics_state: dict[str, list[dict[str, Any]]] = {}


def async_add_external_statistics(
    hass: Any, metadata: StatisticMetaData, data: list[StatisticData]
) -> None:
    """Stub — record what would have been written."""
    imported_metadata.append(metadata)
    imported_data.append(list(data))


def get_last_statistics(
    hass: Any,
    number_of_stats: int,
    statistic_id: str,
    convert_units: bool,
    types: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Stub — return whatever the test pre-populated, or {}."""
    if statistic_id in last_statistics_state:
        return {statistic_id: last_statistics_state[statistic_id]}
    return {}


ha_components_recorder_statistics.async_add_external_statistics = (
    async_add_external_statistics
)
ha_components_recorder_statistics.get_last_statistics = get_last_statistics


# --- recorder root ---------------------------------------------------------


def get_instance(hass: Any) -> Any:
    """Return a stub recorder with an executor that runs jobs inline."""
    rec = MagicMock()

    async def _run(func, *args, **kwargs):
        return func(*args, **kwargs)

    rec.async_add_executor_job = _run
    return rec


ha_components_recorder.get_instance = get_instance


# --- const -----------------------------------------------------------------


class UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"


ha_const.UnitOfEnergy = UnitOfEnergy
ha_const.CONF_EMAIL = "email"
ha_const.CONF_PASSWORD = "password"


class Platform(str, Enum):
    SENSOR = "sensor"


ha_const.Platform = Platform


# --- core ------------------------------------------------------------------


class HomeAssistant:
    """Minimal stand-in carrying the bits the coordinator reads."""

    def __init__(self) -> None:
        self.config = types.SimpleNamespace(currency="USD")


ha_core.HomeAssistant = HomeAssistant


# --- config_entries --------------------------------------------------------


@dataclass
class ConfigEntry:
    data: dict[str, Any] = field(default_factory=dict)


ha_config_entries.ConfigEntry = ConfigEntry


# --- helpers.update_coordinator -------------------------------------------


class DataUpdateCoordinator:
    """Stub that captures init kwargs and is generic-friendly."""

    def __init__(self, hass: Any, logger: Any, name: str, update_interval: Any) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

    def __class_getitem__(cls, item):  # noqa: D105
        return cls


class UpdateFailed(Exception):
    """Stub."""


ha_helpers_update.DataUpdateCoordinator = DataUpdateCoordinator
ha_helpers_update.UpdateFailed = UpdateFailed


# --- util.dt ---------------------------------------------------------------


def start_of_local_day(value: datetime) -> datetime:
    """Return midnight in local TZ. Tests run in UTC for determinism."""
    return value.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def utc_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


ha_util_dt.start_of_local_day = start_of_local_day
ha_util_dt.utc_from_timestamp = utc_from_timestamp


# --- register everything --------------------------------------------------

for name, mod in {
    "homeassistant": ha,
    "homeassistant.components": ha_components,
    "homeassistant.components.recorder": ha_components_recorder,
    "homeassistant.components.recorder.models": ha_components_recorder_models,
    "homeassistant.components.recorder.statistics": (ha_components_recorder_statistics),
    "homeassistant.const": ha_const,
    "homeassistant.core": ha_core,
    "homeassistant.config_entries": ha_config_entries,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.update_coordinator": ha_helpers_update,
    "homeassistant.util": ha_util,
    "homeassistant.util.dt": ha_util_dt,
}.items():
    sys.modules[name] = mod


def reset_capture() -> None:
    """Clear captured stub state between tests."""
    imported_metadata.clear()
    imported_data.clear()
    last_statistics_state.clear()
