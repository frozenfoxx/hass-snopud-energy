"""API client for Snohomish County PUD (MySnoPUD) portal."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp

from .const import (
    BASE_URL,
    DOWNLOAD_SETTINGS_URL,
    DOWNLOAD_URL,
    FORMAT_CSV,
    INTERVAL_DAILY,
    LOGIN_PAGE_URL,
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

    def _extract_login_form_fields(self, html: str) -> dict[str, str]:
        """Extract the hidden fields needed for the login POST.

        The browser sends exactly these hidden fields from the login form:
        RedirectUrl, LoginErrorMessage, ExternalLogin,
        TwoFactorRendered, SecretQuestionRendered.

        Rather than parsing form boundaries (which can be fragile with
        complex HTML), we extract each known field by name.
        """
        fields: dict[str, str] = {}

        known_fields = [
            "RedirectUrl",
            "LoginErrorMessage",
            "ExternalLogin",
            "TwoFactorRendered",
            "SecretQuestionRendered",
        ]

        for field_name in known_fields:
            # Try name="X" ... value="Y"
            match = re.search(
                r'name=["\']'
                + re.escape(field_name)
                + r'["\'][^>]*value=["\']([^"\']*)["\']',
                html,
                re.IGNORECASE,
            )
            if not match:
                # Try value="Y" ... name="X"
                match = re.search(
                    r'value=["\']([^"\']*)["\'][^>]*name=["\']'
                    + re.escape(field_name)
                    + r'["\']',
                    html,
                    re.IGNORECASE,
                )
            if match:
                fields[field_name] = match.group(1)
            else:
                # Use sensible defaults for fields we know exist
                fields[field_name] = ""

        _LOGGER.debug("Extracted login fields: %s", list(fields.keys()))
        return fields

    async def async_login(self) -> bool:
        """Authenticate with the MySnoPUD portal.

        The portal uses AJAX-style login: the POST to /Home/Login returns
        a JSON response with redirect instructions rather than an HTTP
        302 redirect.  A successful response looks like:
            {"AjaxResults":[{"Action":"Redirect",
              "Value":"https://my.snopud.com/Integration/LoginActions"}],
             "Data":null}
        We then follow that redirect URL to complete the login and
        establish session cookies.

        Returns True on success, raises SnoPUDAuthError on failure.
        """
        session = await self._get_session()

        try:
            # Step 1: GET the login page to collect cookies and form fields
            _LOGGER.debug("Fetching login page at %s", LOGIN_PAGE_URL)
            async with session.get(LOGIN_PAGE_URL, allow_redirects=True) as resp:
                if resp.status != 200:
                    raise SnoPUDConnectionError(
                        f"Failed to load login page: status {resp.status}"
                    )
                login_html = await resp.text()
                login_page_url = str(resp.url)

            _LOGGER.debug(
                "Login page loaded: url=%s, length=%d",
                login_page_url,
                len(login_html),
            )

            # Step 2: Extract hidden fields from the login form only
            form_fields = self._extract_login_form_fields(login_html)
            _LOGGER.debug(
                "Login form hidden fields: %s",
                list(form_fields.keys()),
            )

            # Step 3: Build login POST data — use the exact fields and
            # default values we observed the browser sending.
            # Hardcode defaults rather than regex-extracting to avoid
            # accidentally matching fields from other forms on the page.
            login_data = {
                "RedirectUrl": form_fields.get("RedirectUrl", "") or "",
                "LoginErrorMessage": (form_fields.get("LoginErrorMessage", "") or ""),
                "LoginEmail": self._email,
                "LoginPassword": self._password,
                "ExternalLogin": (form_fields.get("ExternalLogin", "") or "False"),
                "TwoFactorRendered": (
                    form_fields.get("TwoFactorRendered", "") or "False"
                ),
                "SecretQuestionRendered": (
                    form_fields.get("SecretQuestionRendered", "") or "False"
                ),
            }

            # Log the email being used (partially redacted)
            email = self._email
            at_idx = email.find("@")
            if at_idx > 2:
                redacted_email = email[:2] + "***" + email[at_idx:]
            else:
                redacted_email = "***" + email[at_idx:]
            _LOGGER.debug(
                "Login attempt for: %s (length=%d, has_plus=%s)",
                redacted_email,
                len(email),
                "+" in email,
            )
            _LOGGER.debug(
                "Password length: %d",
                len(self._password),
            )
            _LOGGER.debug(
                "Login POST fields: %s",
                [k for k in login_data if k != "LoginPassword"],
            )
            _LOGGER.debug(
                "Extracted form field values: %s",
                {k: v for k, v in form_fields.items()},
            )

            # Step 4: POST to the login endpoint.
            # The server returns JSON with redirect instructions,
            # so we must NOT follow redirects automatically.
            # Include X-Requested-With header as the portal's jQuery
            # processAjax function sends this by default.
            _LOGGER.debug("Posting login form to %s", LOGIN_URL)
            async with session.post(
                LOGIN_URL,
                data=login_data,
                headers={"X-Requested-With": "XMLHttpRequest"},
                allow_redirects=False,
            ) as resp:
                response_text = await resp.text()

                _LOGGER.debug(
                    "Login POST response: status=%s, content_length=%d, body=%s",
                    resp.status,
                    len(response_text),
                    response_text[:500],
                )

                if resp.status != 200:
                    raise SnoPUDAuthError(f"Login failed with status {resp.status}")

                # Parse the JSON response
                try:
                    data = json.loads(response_text)
                except (json.JSONDecodeError, ValueError) as exc:
                    # Not JSON — might be HTML error page
                    _LOGGER.error(
                        "Login returned non-JSON response: %s",
                        response_text[:500],
                    )
                    raise SnoPUDAuthError(
                        "Login failed — unexpected response format"
                    ) from exc

                # Check AjaxResults for a Redirect action
                ajax_results = data.get("AjaxResults", [])
                redirect_url = None
                for result in ajax_results:
                    action = result.get("Action", "")
                    value = result.get("Value", "")
                    if action == "Redirect" and value:
                        redirect_url = value
                        break

                if not redirect_url:
                    # Check for error messages in AjaxResults
                    error_msg = None
                    for result in ajax_results:
                        action = result.get("Action", "")
                        value = result.get("Value", "")
                        if action in (
                            "Error",
                            "Message",
                            "Alert",
                            "ApplyGlobalDanger",
                        ):
                            error_msg = value
                            break

                    # Also check the Data field for login-specific errors
                    resp_data = data.get("Data")
                    if resp_data and isinstance(resp_data, dict):
                        login_error = resp_data.get("LoginErrorMessage")
                        if login_error:
                            error_msg = login_error

                    if error_msg:
                        raise SnoPUDAuthError(f"Login failed: {error_msg}")

                    _LOGGER.error(
                        "Login response had no redirect: %s",
                        response_text[:500],
                    )
                    raise SnoPUDAuthError("Login failed — no redirect in response")

            # Step 5: Follow the redirect URL to complete login
            _LOGGER.debug("Following login redirect to %s", redirect_url)
            async with session.get(redirect_url, allow_redirects=True) as resp:
                final_url = str(resp.url)
                _LOGGER.debug(
                    "Post-login redirect: status=%s, final_url=%s",
                    resp.status,
                    final_url,
                )

            self._authenticated = True
            _LOGGER.info("Successfully authenticated with MySnoPUD")
            return True

        except aiohttp.ClientError as err:
            raise SnoPUDConnectionError(
                f"Connection error during login: {err}"
            ) from err

    async def async_get_usage_data(
        self,
        interval: str = INTERVAL_DAILY,
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

        try:
            # Step 1: Initialize download settings via AJAX endpoint.
            # This returns JSON containing the download form HTML,
            # which includes hidden fields we need for the POST.
            _LOGGER.debug("Initializing download settings at %s", DOWNLOAD_SETTINGS_URL)
            async with session.get(
                DOWNLOAD_SETTINGS_URL,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{BASE_URL}/Usage",
                },
                allow_redirects=True,
            ) as resp:
                settings_text = await resp.text()
                _LOGGER.debug(
                    "Download settings response: status=%s, url=%s, content_length=%d",
                    resp.status,
                    str(resp.url),
                    len(settings_text),
                )

                # Log session cookies for debugging
                cookie_names = [c.key for c in session.cookie_jar]
                _LOGGER.debug(
                    "Session cookies after download settings: %s",
                    cookie_names,
                )

                if resp.status != 200:
                    self._authenticated = False
                    raise SnoPUDAuthError(
                        f"Failed to load download settings: {resp.status}"
                    )

            # Parse the JSON response to extract the form HTML
            form_html = settings_text
            try:
                settings_json = json.loads(settings_text)
                ajax_results = settings_json.get("AjaxResults", [])
                for result in ajax_results:
                    if result.get("Action") == "Replace":
                        form_html = result.get("Value", "")
                        break

                    # Check for redirect (session expired)
                    if result.get("Action") == "Redirect":
                        redirect_val = result.get("Value", "")
                        if "Login" in redirect_val:
                            self._authenticated = False
                            raise SnoPUDAuthError(
                                "Session expired during download setup"
                            )
            except (json.JSONDecodeError, ValueError):
                # Not JSON, use as-is
                pass

            # Extract hidden fields from the download form,
            # especially __RequestVerificationToken (required by
            # ASP.NET anti-forgery validation).
            download_hidden: dict[str, str] = {}

            # Pattern 1: name="X" ... value="Y" (most common)
            hidden_pattern = (
                r'<input[^>]*name=["\']([^"\']+)["\'][^>]*'
                r'value=["\']([^"\']*)["\'][^>]*/?>'
            )
            for match in re.finditer(hidden_pattern, form_html, re.IGNORECASE):
                name = match.group(1)
                value = match.group(2)
                if name == "__RequestVerificationToken":
                    download_hidden[name] = value

            # Pattern 2 fallback: value="Y" ... name="X"
            if "__RequestVerificationToken" not in download_hidden:
                rev_pattern = (
                    r'<input[^>]*value=["\']([^"\']*)["\'][^>]*'
                    r'name=["\']([^"\']+)["\'][^>]*/?>'
                )
                for match in re.finditer(rev_pattern, form_html, re.IGNORECASE):
                    value = match.group(1)
                    name = match.group(2)
                    if name == "__RequestVerificationToken":
                        download_hidden[name] = value

            # Pattern 3 fallback: targeted search for the token
            if "__RequestVerificationToken" not in download_hidden:
                token_match = re.search(
                    r"__RequestVerificationToken[^>]*"
                    r'value=["\']([^"\']+)["\']',
                    form_html,
                    re.IGNORECASE,
                )
                if token_match:
                    download_hidden["__RequestVerificationToken"] = token_match.group(1)

            # Also extract the form action URL
            form_action_match = re.search(
                r'<form[^>]*id=["\']downloadOptions["\'][^>]*'
                r'action=["\']([^"\']+)["\']',
                form_html,
                re.IGNORECASE,
            )
            download_url = DOWNLOAD_URL
            if form_action_match:
                action = form_action_match.group(1)
                if action.startswith("/"):
                    download_url = f"{BASE_URL}{action}"
                elif action.startswith("http"):
                    download_url = action
                _LOGGER.debug("Download form action URL: %s", download_url)

            _LOGGER.debug(
                "Download form hidden fields: %s (token_len=%s)",
                list(download_hidden.keys()),
                len(download_hidden.get("__RequestVerificationToken", "")),
            )
            if "__RequestVerificationToken" not in download_hidden:
                _LOGGER.warning(
                    "No __RequestVerificationToken found in "
                    "download form — POST may fail",
                )

            # Extract ALL input/select field names from the form
            # to discover the correct field names for dates, etc.
            all_field_names: list[str] = []
            for m in re.finditer(
                r"<(?:input|select|textarea)[^>]*"
                r'name=["\']([^"\']+)["\']',
                form_html,
                re.IGNORECASE,
            ):
                all_field_names.append(m.group(1))
            _LOGGER.debug(
                "All download form field names: %s",
                all_field_names,
            )

            # Step 2: Build the download POST data.
            # Extract only the specific dynamic values we need
            # from the form HTML (meter values, column/row option
            # metadata), and build the rest manually using the
            # correct field names discovered from the form.

            def _extract_field(name: str, html: str, default: str = "") -> str:
                """Extract a single named field's value."""
                m = re.search(
                    r'name=["\']'
                    + re.escape(name)
                    + r'["\'][^>]*value=["\']([^"\']*)["\']',
                    html,
                    re.IGNORECASE,
                )
                if not m:
                    m = re.search(
                        r'value=["\']([^"\']*)["\'][^>]*name=["\']'
                        + re.escape(name)
                        + r'["\']',
                        html,
                        re.IGNORECASE,
                    )
                return m.group(1) if m else default

            def _extract_selected(name: str, html: str, default: str = "") -> str:
                """Extract selected value from a <select>.

                Falls back to the first <option> value if none is
                marked as selected.
                """
                sel = re.search(
                    r'<select[^>]*name=["\']'
                    + re.escape(name)
                    + r'["\'][^>]*>.*?</select>',
                    html,
                    re.IGNORECASE | re.DOTALL,
                )
                if sel:
                    # Try explicitly selected option first
                    opt = re.search(
                        r"<option[^>]*selected[^>]*"
                        r'value=["\']([^"\']*)["\']',
                        sel.group(0),
                        re.IGNORECASE,
                    )
                    if opt:
                        return opt.group(1)
                    # Fall back to first option with a value
                    first = re.search(
                        r'<option[^>]*value=["\']([^"\']+)["\']',
                        sel.group(0),
                        re.IGNORECASE,
                    )
                    if first:
                        return first.group(1)
                return default

            # Extract meter info (value + selected state)
            meters: list[dict[str, str]] = []
            for i in range(10):
                val = _extract_field(f"Meters[{i}].Value", form_html)
                if not val and i >= 2:
                    break
                meters.append(
                    {
                        "Value": val,
                        "Selected": "true",  # Select all meters
                    }
                )

            # Extract column options (value + name + checked state).
            # The .Name and .Value fields use internal identifiers
            # like "ReadDate", "Consumption", "Dollar" — not the
            # display labels "Read Date", "kWh", "$".
            columns: list[dict[str, str]] = []
            wanted_columns = {
                "ReadDate",
                "Consumption",
                "Dollar",
            }
            for i in range(20):
                col_name = _extract_field(f"ColumnOptions[{i}].Name", form_html)
                if not col_name:
                    break
                col_value = _extract_field(f"ColumnOptions[{i}].Value", form_html)
                columns.append(
                    {
                        "Value": col_value,
                        "Name": col_name,
                        "Checked": ("true" if col_value in wanted_columns else "false"),
                    }
                )

            # Extract row/sort options (value + name + desc)
            rows: list[dict[str, str]] = []
            for i in range(10):
                row_name = _extract_field(f"RowOptions[{i}].Name", form_html)
                if not row_name:
                    break
                rows.append(
                    {
                        "Value": _extract_field(f"RowOptions[{i}].Value", form_html),
                        "Name": row_name,
                        "Desc": _extract_field(f"RowOptions[{i}].Desc", form_html),
                    }
                )

            # Get the selected usage type from the form
            selected_usage = _extract_selected("SelectedUsageType", form_html, "")

            _LOGGER.debug(
                "Form extraction: meters=%d, columns=%d, rows=%d, usage_type=%s",
                len(meters),
                len(columns),
                len(rows),
                selected_usage,
            )

            # Build the form data dict
            form_data: dict[str, str] = {
                "__RequestVerificationToken": download_hidden.get(
                    "__RequestVerificationToken", ""
                ),
                "HasMultipleUsageTypes": _extract_field(
                    "HasMultipleUsageTypes", form_html, "True"
                ),
                "FileFormat": _extract_field("FileFormat", form_html, ""),
                "SelectedFormat": FORMAT_CSV,
                "ThirdPartyPODID": _extract_field("ThirdPartyPODID", form_html, ""),
                "SelectedServiceType": SERVICE_TYPE_ELECTRIC,
                "SelectedInterval": interval,
                "SelectedUsageType": selected_usage,
                "Start": start_date.strftime("%m/%d/%Y"),
                "End": end_date.strftime("%m/%d/%Y"),
            }

            # Add meter fields
            for i, meter in enumerate(meters):
                form_data[f"Meters[{i}].Value"] = meter["Value"]
                form_data[f"Meters[{i}].Selected"] = meter["Selected"]

            # Add column option fields
            for i, col in enumerate(columns):
                form_data[f"ColumnOptions[{i}].Value"] = col["Value"]
                form_data[f"ColumnOptions[{i}].Name"] = col["Name"]
                form_data[f"ColumnOptions[{i}].Checked"] = col["Checked"]

            # Add row option fields
            for i, row in enumerate(rows):
                form_data[f"RowOptions[{i}].Value"] = row["Value"]
                form_data[f"RowOptions[{i}].Name"] = row["Name"]
                form_data[f"RowOptions[{i}].Desc"] = row["Desc"]

            _LOGGER.debug(
                "Posting download request to %s with %d fields",
                download_url,
                len(form_data),
            )
            # Do NOT send X-Requested-With here — the download
            # endpoint returns a CSV file via regular form POST.
            # Sending the AJAX header causes the server's AJAX
            # middleware to intercept and return JSON redirects
            # instead of the file.
            async with session.post(
                download_url,
                data=form_data,
                headers={
                    "Referer": f"{BASE_URL}/Usage",
                    "Origin": BASE_URL,
                },
                allow_redirects=True,
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                response_text = await resp.text()

                _LOGGER.debug(
                    "Download response: status=%s, content_type=%s, content_length=%d",
                    resp.status,
                    content_type,
                    len(response_text),
                )

                if resp.status != 200:
                    if resp.status in (302, 401, 403):
                        self._authenticated = False
                        raise SnoPUDAuthError("Session expired")
                    raise SnoPUDError(f"Download failed with status {resp.status}")

                # Check if response is JSON (AJAX redirect or error)
                try:
                    resp_json = json.loads(response_text)
                    ajax_results = resp_json.get("AjaxResults", [])
                    for result in ajax_results:
                        action = result.get("Action", "")
                        value = result.get("Value", "")
                        if action == "Redirect" and "Login" in value:
                            self._authenticated = False
                            raise SnoPUDAuthError(
                                "Session expired — download redirected to login"
                            )
                    # If it's JSON but not a redirect, it's an error
                    _LOGGER.error(
                        "Download returned JSON instead of CSV: %s",
                        response_text[:500],
                    )
                    raise SnoPUDError("Download returned unexpected JSON response")
                except (json.JSONDecodeError, ValueError):
                    # Not JSON — hopefully it's CSV data
                    pass

                # If we got HTML back, it's either a session
                # expiry (login page) or a server error page.
                if "text/html" in content_type:
                    if "Login" in response_text[:2000]:
                        self._authenticated = False
                        raise SnoPUDAuthError("Session expired — redirected to login")
                    _LOGGER.error(
                        "Expected CSV but got HTML error: %s",
                        response_text[:1000],
                    )
                    raise SnoPUDError("Server returned an error page instead of CSV")

                return self._parse_csv(response_text)

        except aiohttp.ClientError as err:
            raise SnoPUDConnectionError(
                f"Connection error during data fetch: {err}"
            ) from err

    @staticmethod
    def _parse_date(date_str: str) -> str:
        """Normalise a date string to MM/DD/YYYY.

        The portal returns dates in several formats depending on
        the selected interval:
          - "03/17/2026 12:00:00 AM" (billing)
          - "03/17/2026"             (daily)
        """
        date_str = date_str.strip().strip('"')
        if not date_str:
            return ""
        # Try datetime with time component first
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%m/%d/%Y")
            except ValueError:
                continue
        return date_str

    def _parse_csv(self, csv_data: str) -> SnoPUDAccountData:
        """Parse the CSV download into structured data."""
        account_data = SnoPUDAccountData(last_updated=datetime.now())

        reader = csv.DictReader(io.StringIO(csv_data))
        headers = reader.fieldnames or []
        _LOGGER.debug("CSV headers: %s", headers)

        # The date column name varies by interval:
        #   Billing → "End", Daily → "Read Date" or "Start"
        date_col = None
        for candidate in ("Read Date", "End", "Start", "ReadDate"):
            if candidate in headers:
                date_col = candidate
                break
        if date_col is None and headers:
            # Use the first column as a fallback
            date_col = headers[0]
            _LOGGER.warning("No known date column found; using '%s'", date_col)

        for row in reader:
            try:
                raw_date = row.get(date_col, "") if date_col else ""
                read_date = self._parse_date(raw_date)
                kwh_str = row.get("kWh", "0").strip().strip('"')
                cost_str = row.get("$", "0").strip().strip('"').lstrip("$")

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
