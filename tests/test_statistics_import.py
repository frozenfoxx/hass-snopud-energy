"""Verify the coordinator's _async_import_statistics math.

These tests exercise the cumulative-sum logic and the dedup-against-
previous-import behavior — the parts most likely to silently produce
wrong values in the Energy dashboard if regressed.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root so `custom_components.snopud_energy` imports cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conftest

from custom_components.snopud_energy.const import (
    STAT_ID_COST,
    STAT_ID_ENERGY,
)
from custom_components.snopud_energy.coordinator import SnoPUDCoordinator
from custom_components.snopud_energy.snopud_api import (
    SnoPUDAccountData,
    SnoPUDMeterReading,
)


def _make_coordinator() -> SnoPUDCoordinator:
    """Build a coordinator without reaching into the SnoPUD API."""
    hass = conftest.HomeAssistant()
    entry = conftest.ConfigEntry(data={"email": "x@x", "password": "p"})
    return SnoPUDCoordinator(hass, entry)


def _data(*tuples: tuple[str, float, float]) -> SnoPUDAccountData:
    return SnoPUDAccountData(
        readings=[SnoPUDMeterReading(read_date=d, kwh=k, cost=c) for d, k, c in tuples]
    )


def _run(coord: SnoPUDCoordinator, data: SnoPUDAccountData) -> None:
    asyncio.run(coord._async_import_statistics(data))


def test_first_import_builds_monotonic_cumulative_sums():
    """Sums must be monotonically increasing day-over-day."""
    conftest.reset_capture()
    coord = _make_coordinator()

    _run(
        coord,
        _data(
            ("05/01/2026", 10.0, 1.50),
            ("05/02/2026", 12.0, 1.80),
            ("05/03/2026", 8.0, 1.20),
        ),
    )

    assert len(conftest.imported_metadata) == 2  # energy + cost
    energy_meta, cost_meta = conftest.imported_metadata
    energy_pts, cost_pts = conftest.imported_data

    assert energy_meta.statistic_id == STAT_ID_ENERGY
    assert cost_meta.statistic_id == STAT_ID_COST

    # Energy: 10, 22, 30 (running cumulative)
    assert [p.sum for p in energy_pts] == [10.0, 22.0, 30.0]
    # Cost: 1.50, 3.30, 4.50
    assert [round(p.sum, 2) for p in cost_pts] == [1.50, 3.30, 4.50]

    # Day-over-day deltas (what the Energy dashboard actually shows)
    # must equal each day's raw kWh.
    deltas = [
        energy_pts[i].sum - (energy_pts[i - 1].sum if i else 0)
        for i in range(len(energy_pts))
    ]
    assert deltas == [10.0, 12.0, 8.0]


def test_unsorted_input_is_normalized_to_chronological_order():
    """The coordinator must sort regardless of CSV ordering."""
    conftest.reset_capture()
    coord = _make_coordinator()

    # Intentionally scrambled (most-recent-first, like SnoPUD often returns).
    _run(
        coord,
        _data(
            ("05/03/2026", 8.0, 1.20),
            ("05/01/2026", 10.0, 1.50),
            ("05/02/2026", 12.0, 1.80),
        ),
    )

    energy_pts = conftest.imported_data[0]
    starts = [p.start for p in energy_pts]
    assert starts == sorted(starts)
    assert [p.sum for p in energy_pts] == [10.0, 22.0, 30.0]


def test_subsequent_import_continues_from_last_sum_and_skips_known_days():
    """The second poll must continue the meter, not restart at zero."""
    conftest.reset_capture()
    coord = _make_coordinator()

    # Simulate a prior import that already covered May 1-3.
    last_day = datetime(2026, 5, 3, tzinfo=timezone.utc)
    conftest.last_statistics_state[STAT_ID_ENERGY] = [
        {"start": last_day.timestamp(), "sum": 30.0}
    ]
    conftest.last_statistics_state[STAT_ID_COST] = [
        {"start": last_day.timestamp(), "sum": 4.50}
    ]

    # Next poll returns the same window plus two new days.
    _run(
        coord,
        _data(
            ("05/01/2026", 10.0, 1.50),
            ("05/02/2026", 12.0, 1.80),
            ("05/03/2026", 8.0, 1.20),
            ("05/04/2026", 11.0, 1.65),
            ("05/05/2026", 9.0, 1.35),
        ),
    )

    energy_pts = conftest.imported_data[0]
    cost_pts = conftest.imported_data[1]

    # Only May 4 and May 5 should be pushed (May 1-3 are <= last_energy_ts).
    assert len(energy_pts) == 2
    assert [p.sum for p in energy_pts] == [41.0, 50.0]  # 30 + 11, 41 + 9
    assert [round(p.sum, 2) for p in cost_pts] == [6.15, 7.50]


def test_empty_or_unparseable_readings_short_circuits():
    """No readings -> no statistics calls."""
    conftest.reset_capture()
    coord = _make_coordinator()

    _run(coord, SnoPUDAccountData(readings=[]))
    assert conftest.imported_metadata == []

    # Garbage dates should also produce nothing without raising.
    _run(coord, _data(("not-a-date", 5.0, 0.50)))
    assert conftest.imported_metadata == []


def test_cost_unit_uses_configured_currency():
    """Cost metadata should reflect hass.config.currency, not hard-coded USD."""
    conftest.reset_capture()
    coord = _make_coordinator()
    coord.hass.config.currency = "EUR"

    _run(coord, _data(("05/01/2026", 10.0, 1.50)))

    cost_meta = conftest.imported_metadata[1]
    assert cost_meta.unit_of_measurement == "EUR"
