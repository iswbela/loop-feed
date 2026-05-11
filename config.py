import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- arquivos de dados ---
CONFIG_FILE = os.path.join(BASE_DIR, "kommons_config.json")   # legado
MESSAGES_FILE = os.path.join(BASE_DIR, "kommons_messages.json")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")

# --- Kommons ---
BASE_URL = "https://kommons.com"
LOGIN_URL = f"{BASE_URL}/members-area/login"
DASHBOARD_URL = f"{BASE_URL}/members-area"

# --- VivaStreet ---
VIVASTREET_BASE_URL      = "https://www.vivastreet.co.uk"
VIVASTREET_LOGIN_URL     = f"{VIVASTREET_BASE_URL}/user/login"     # URL real da página de login
VIVASTREET_API_BASE_URL  = f"{VIVASTREET_BASE_URL}/api/v1"
VIVASTREET_API_LOGIN_URL = f"{VIVASTREET_API_BASE_URL}/auth/user/login"
VIVASTREET_API_REFRESH_URL = f"{VIVASTREET_API_BASE_URL}/auth/user/refresh"

APP_VERSION = "1.1.0"

HEART = "❤️"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
