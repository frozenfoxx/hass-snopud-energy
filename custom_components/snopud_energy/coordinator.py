"""DataUpdateCoordinator for SnoPUD Energy."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN
from .snopud_api import (
    SnoPUDAccountData,
    SnoPUDApiClient,
    SnoPUDAuthError,
    SnoPUDConnectionError,
    SnoPUDError,
)

_LOGGER = logging.getLogger(__name__)


class SnoPUDCoordinator(DataUpdateCoordinator[SnoPUDAccountData]):
    """Coordinator to manage fetching SnoPUD data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.api = SnoPUDApiClient(
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
        )

    async def _async_update_data(self) -> SnoPUDAccountData:
        """Fetch data from the SnoPUD portal."""
        try:
            return await self.api.async_get_usage_data()
        except SnoPUDAuthError as err:
            # Try re-authenticating once
            _LOGGER.debug("Auth error, attempting re-login: %s", err)
            try:
                await self.api.async_login()
                return await self.api.async_get_usage_data()
            except SnoPUDAuthError as auth_err:
                raise UpdateFailed(f"Authentication failed: {auth_err}") from auth_err
        except SnoPUDConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except SnoPUDError as err:
            raise UpdateFailed(f"Error fetching SnoPUD data: {err}") from err

    async def async_shutdown(self) -> None:
        """Close the API client session."""
        await self.api.async_close()
