"""Constants for the SnoPUD Energy integration."""

DOMAIN = "snopud_energy"

CONF_ACCOUNT_NUMBER = "account_number"

BASE_URL = "https://my.snopud.com"
LOGIN_PAGE_URL = BASE_URL
LOGIN_URL = f"{BASE_URL}/Home/Login"
DASHBOARD_TABLE_URL = f"{BASE_URL}/Dashboard/Table"
DOWNLOAD_SETTINGS_URL = f"{BASE_URL}/Usage/InitializeDownloadSettings"
DOWNLOAD_URL = f"{BASE_URL}/Usage/Download"

DEFAULT_SCAN_INTERVAL_HOURS = 12

# Download form field values
FORMAT_CSV = "2"
FORMAT_GREEN_BUTTON = "1"
SERVICE_TYPE_ELECTRIC = "1"
INTERVAL_15_MIN = "3"
INTERVAL_30_MIN = "4"
INTERVAL_HOURLY = "5"
INTERVAL_DAILY = "6"
INTERVAL_WEEKLY = "8"
INTERVAL_BILLING = "7"
