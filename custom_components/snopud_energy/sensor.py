"""Sensor platform for SnoPUD Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SnoPUDCoordinator
from .snopud_api import SnoPUDAccountData


@dataclass(frozen=True, kw_only=True)
class SnoPUDSensorEntityDescription(SensorEntityDescription):
    """Describe a SnoPUD sensor entity."""

    value_fn: Callable[[SnoPUDAccountData], float | str | None]


SENSOR_DESCRIPTIONS: tuple[SnoPUDSensorEntityDescription, ...] = (
    SnoPUDSensorEntityDescription(
        key="latest_energy",
        translation_key="latest_energy",
        name="Latest Billing Period Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.latest_kwh,
    ),
    SnoPUDSensorEntityDescription(
        key="latest_cost",
        translation_key="latest_cost",
        name="Latest Billing Period Cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.latest_cost,
    ),
    SnoPUDSensorEntityDescription(
        key="current_month_energy",
        translation_key="current_month_energy",
        name="Current Month Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: data.total_kwh_current_month,
    ),
    SnoPUDSensorEntityDescription(
        key="current_month_cost",
        translation_key="current_month_cost",
        name="Current Month Cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.total_cost_current_month,
    ),
    SnoPUDSensorEntityDescription(
        key="last_read_date",
        translation_key="last_read_date",
        name="Last Read Date",
        icon="mdi:calendar",
        value_fn=lambda data: data.latest_read_date,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SnoPUD sensor entities from a config entry."""
    coordinator: SnoPUDCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        SnoPUDSensorEntity(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class SnoPUDSensorEntity(CoordinatorEntity[SnoPUDCoordinator], SensorEntity):
    """Representation of a SnoPUD sensor."""

    entity_description: SnoPUDSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnoPUDCoordinator,
        description: SnoPUDSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.config_entry.entry_id)},
            "name": "Snohomish County PUD",
            "manufacturer": "Snohomish County PUD",
            "model": "MySnoPUD",
            "entry_type": "service",
        }

    @property
    def native_value(self) -> float | str | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return additional state attributes."""
        if self.coordinator.data is None:
            return None
        return {
            "last_updated": (
                self.coordinator.data.last_updated.isoformat()
                if self.coordinator.data.last_updated
                else None
            ),
            "reading_count": len(self.coordinator.data.readings),
        }
