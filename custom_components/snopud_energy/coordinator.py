"""DataUpdateCoordinator for SnoPUD Energy."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    STAT_ID_COST,
    STAT_ID_ENERGY,
)
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
            data = await self.api.async_get_usage_data()
        except SnoPUDAuthError as err:
            _LOGGER.debug("Auth error, attempting re-login: %s", err)
            try:
                await self.api.async_login()
                data = await self.api.async_get_usage_data()
            except SnoPUDAuthError as auth_err:
                raise UpdateFailed(f"Authentication failed: {auth_err}") from auth_err
        except SnoPUDConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except SnoPUDError as err:
            raise UpdateFailed(f"Error fetching SnoPUD data: {err}") from err

        await self._async_import_statistics(data)
        return data

    async def _async_import_statistics(self, data: SnoPUDAccountData) -> None:
        """Push daily readings to the recorder as long-term statistics.

        The Energy dashboard reads from the long-term statistics tables
        rather than from sensor state changes. A per-day kWh value
        exposed as a TOTAL sensor produces nonsense (often negative)
        daily bars because HA computes consumption as the delta between
        consecutive sensor states, treating the sensor as a cumulative
        meter. Importing the readings as statistics with a monotonically
        increasing sum gives the dashboard the shape it expects and
        backfills history that would otherwise be lost between polls.
        """
        if not data.readings:
            return

        parsed: list[tuple[datetime, float, float]] = []
        for reading in data.readings:
            try:
                day = datetime.strptime(reading.read_date, "%m/%d/%Y")
            except ValueError:
                continue
            parsed.append((day, reading.kwh, reading.cost))
        parsed.sort(key=lambda x: x[0])

        if not parsed:
            return

        recorder = get_instance(self.hass)
        last_energy = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, STAT_ID_ENERGY, False, {"sum"}
        )
        last_cost = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, STAT_ID_COST, False, {"sum"}
        )

        energy_sum = 0.0
        cost_sum = 0.0
        last_energy_ts: float | None = None
        if last_energy and STAT_ID_ENERGY in last_energy:
            entry = last_energy[STAT_ID_ENERGY][0]
            energy_sum = entry.get("sum") or 0.0
            last_energy_ts = entry.get("start")
        if last_cost and STAT_ID_COST in last_cost:
            cost_sum = last_cost[STAT_ID_COST][0].get("sum") or 0.0

        energy_stats: list[StatisticData] = []
        cost_stats: list[StatisticData] = []

        for day, kwh, cost in parsed:
            day_start = dt_util.start_of_local_day(day)
            # The recorder dedupes on `start`, but skipping known days
            # also avoids re-adding their kwh to the running sum.
            if last_energy_ts is not None and day_start.timestamp() <= last_energy_ts:
                continue

            energy_sum += kwh
            cost_sum += cost
            energy_stats.append(
                StatisticData(start=day_start, state=energy_sum, sum=energy_sum)
            )
            cost_stats.append(
                StatisticData(start=day_start, state=cost_sum, sum=cost_sum)
            )

        if not energy_stats:
            return

        async_add_external_statistics(
            self.hass,
            StatisticMetaData(
                has_mean=False,
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name="Snohomish County PUD Energy Consumption",
                source=DOMAIN,
                statistic_id=STAT_ID_ENERGY,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            energy_stats,
        )
        async_add_external_statistics(
            self.hass,
            StatisticMetaData(
                has_mean=False,
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name="Snohomish County PUD Energy Cost",
                source=DOMAIN,
                statistic_id=STAT_ID_COST,
                unit_of_measurement=self.hass.config.currency,
            ),
            cost_stats,
        )

        _LOGGER.debug(
            "Imported %d daily stats (energy_sum=%.2f kWh, cost_sum=%.2f USD)",
            len(energy_stats),
            energy_sum,
            cost_sum,
        )

    async def async_shutdown(self) -> None:
        """Close the API client session."""
        await self.api.async_close()
