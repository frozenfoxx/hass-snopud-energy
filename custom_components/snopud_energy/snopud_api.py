"""API client for Snohomish County PUD (MySnoPUD) portal."""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp

from .const import (
    DOWNLOAD_URL,
    FORMAT_CSV,
    INTERVAL_BILLING,
    LOGIN_URL,
    SERVICE_TYPE_ELECTRIC,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class SnoPUDAuthError(Exception):
    """Raised when authentication fails."""


class SnoPUDConnectionError(Exception):
    """Raised when a connection error occurs."""


class SnoPUDError(Exception):
    """Raised for general API errors."""


@dataclass
class SnoPUDMeterReading:
    """A single meter reading from the portal."""

    read_date: str
    kwh: float
    cost: float
    meter_number: str = ""
    estimated: bool = False


@dataclass
class SnoPUDAccountData:
    """Aggregated account data from a scrape."""

    readings: list[SnoPUDMeterReading] = field(default_factory=list)
    latest_kwh: float | None = None
    latest_cost: float | None = None
    latest_read_date: str | None = None
    total_kwh_current_month: float = 0.0
    total_cost_current_month: float = 0.0
    last_updated: datetime | None = None


class SnoPUDApiClient:
    """Client for interacting with the MySnoPUD portal."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._session = session
        self._owns_session = session is None
        self._authenticated = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(
                cookie_jar=jar,
                headers={"User-Agent": USER_AGENT},
            )
            self._owns_session = True
            self._authenticated = False
        return self._session

    async def async_login(self) -> bool:
        """Authenticate with the MySnoPUD portal.

        Returns True on success, raises SnoPUDAuthError on failure.
        """
        session = await self._get_session()

        login_data = {
            "LoginEmail": self._email,
            "LoginPassword": self._password,
            "RememberMe": "true",
            "RedirectUrl": "",
            "LoginErrorMessage": "",
            "ExternalLogin": "False",
            "TwoFactorRendered": "",
            "SecretQuestionRendered": "",
        }

        try:
            async with session.post(
                LOGIN_URL,
                data=login_data,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    raise SnoPUDAuthError(f"Login failed with status {resp.status}")

                final_url = str(resp.url)
                response_text = await resp.text()

                # Successful login redirects to /Dashboard
                if "Dashboard" in final_url:
                    self._authenticated = True
                    _LOGGER.debug("Successfully authenticated with MySnoPUD")
                    return True

                # Check for error messages in the response
                if (
                    "LoginErrorMessage" in response_text
                    or "invalid" in response_text.lower()
                ):
                    raise SnoPUDAuthError("Invalid email or password")

                # If we're still on the login page, auth likely failed
                if "LoginEmail" in response_text:
                    raise SnoPUDAuthError("Login failed — still on login page")

                # Might have landed on a 2FA or secret question page
                if "TwoFactor" in response_text or "SecretQuestion" in response_text:
                    raise SnoPUDAuthError(
                        "Account requires two-factor authentication or "
                        "secret question, which is not yet supported"
                    )

                self._authenticated = True
                return True

        except aiohttp.ClientError as err:
            raise SnoPUDConnectionError(
                f"Connection error during login: {err}"
            ) from err

    async def async_get_usage_data(
        self,
        interval: str = INTERVAL_BILLING,
        days_back: int = 60,
    ) -> SnoPUDAccountData:
        """Fetch usage data from the portal.

        Args:
            interval: The data interval (use INTERVAL_* constants).
            days_back: How many days of history to request.

        Returns:
            SnoPUDAccountData with parsed readings.
        """
        if not self._authenticated:
            await self.async_login()

        session = await self._get_session()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        form_data = {
            "SelectedFormat": FORMAT_CSV,
            "SelectedServiceType": SERVICE_TYPE_ELECTRIC,
            "SelectedInterval": interval,
            "StartDate": start_date.strftime("%m/%d/%Y"),
            "EndDate": end_date.strftime("%m/%d/%Y"),
            "HasMultipleUsageTypes": "True",
            "FileFormat": "",
            "ThirdPartyPODID": "",
            # Select both meters by default; the portal ignores
            # entries for meters that don't exist on the account.
            "Meters[0].Selected": "true",
            "Meters[1].Selected": "true",
            "Meters[2].Selected": "true",
            "Meters[3].Selected": "true",
            # Column options
            "ColumnOptions[0].Checked": "true",  # Read Date
            "ColumnOptions[0].Name": "Read Date",
            "ColumnOptions[1].Checked": "false",
            "ColumnOptions[1].Name": "Account Number",
            "ColumnOptions[2].Checked": "false",
            "ColumnOptions[2].Name": "Name",
            "ColumnOptions[3].Checked": "false",
            "ColumnOptions[3].Name": "Meter",
            "ColumnOptions[4].Checked": "false",
            "ColumnOptions[4].Name": "Location",
            "ColumnOptions[5].Checked": "false",
            "ColumnOptions[5].Name": "Address",
            "ColumnOptions[6].Checked": "false",
            "ColumnOptions[6].Name": "Estimated Indicator",
            "ColumnOptions[7].Checked": "true",  # kWh
            "ColumnOptions[7].Name": "kWh",
            "ColumnOptions[8].Checked": "true",  # $
            "ColumnOptions[8].Name": "$",
            # Sort
            "SortOptions[0].Name": "Read Date",
            "SortOptions[0].IsAscending": "true",
            "SortOptions[1].Name": "kWh",
            "SortOptions[1].IsAscending": "true",
            "SortOptions[2].Name": "$",
            "SortOptions[2].IsAscending": "true",
        }

        try:
            async with session.post(
                DOWNLOAD_URL,
                data=form_data,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    # Session might have expired
                    if resp.status in (302, 401, 403):
                        self._authenticated = False
                        _LOGGER.warning(
                            "Session expired, will re-authenticate on next attempt"
                        )
                        raise SnoPUDAuthError("Session expired")
                    raise SnoPUDError(f"Download failed with status {resp.status}")

                content_type = resp.headers.get("Content-Type", "")

                # If we got HTML back instead of a CSV, session likely expired
                if "text/html" in content_type:
                    self._authenticated = False
                    raise SnoPUDAuthError(
                        "Session expired — received HTML instead of CSV"
                    )

                csv_data = await resp.text()
                return self._parse_csv(csv_data)

        except aiohttp.ClientError as err:
            raise SnoPUDConnectionError(
                f"Connection error during data fetch: {err}"
            ) from err

    def _parse_csv(self, csv_data: str) -> SnoPUDAccountData:
        """Parse the CSV download into structured data."""
        account_data = SnoPUDAccountData(last_updated=datetime.now())

        reader = csv.DictReader(io.StringIO(csv_data))

        for row in reader:
            try:
                # The CSV columns depend on what we requested.
                # We asked for: Read Date, kWh, $
                read_date = row.get("Read Date", "").strip()
                kwh_str = row.get("kWh", "0").strip()
                cost_str = row.get("$", "0").strip()

                # Handle empty or malformed values
                kwh = float(kwh_str) if kwh_str else 0.0
                cost = float(cost_str) if cost_str else 0.0

                reading = SnoPUDMeterReading(
                    read_date=read_date,
                    kwh=kwh,
                    cost=cost,
                )
                account_data.readings.append(reading)

            except (ValueError, KeyError) as err:
                _LOGGER.debug("Skipping malformed CSV row: %s — %s", row, err)
                continue

        if account_data.readings:
            # Latest reading (sorted by date ascending, so last is newest)
            latest = account_data.readings[-1]
            account_data.latest_kwh = latest.kwh
            account_data.latest_cost = latest.cost
            account_data.latest_read_date = latest.read_date

            # Sum current month
            now = datetime.now()
            for reading in account_data.readings:
                try:
                    rd = datetime.strptime(reading.read_date, "%m/%d/%Y")
                    if rd.year == now.year and rd.month == now.month:
                        account_data.total_kwh_current_month += reading.kwh
                        account_data.total_cost_current_month += reading.cost
                except ValueError:
                    continue

        _LOGGER.debug("Parsed %d readings from SnoPUD CSV", len(account_data.readings))
        return account_data

    async def async_close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
