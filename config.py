import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "kommons_config.json")
MESSAGES_FILE = os.path.join(BASE_DIR, "kommons_messages.json")

BASE_URL = "https://kommons.com"
LOGIN_URL = f"{BASE_URL}/members-area/login"
DASHBOARD_URL = f"{BASE_URL}/members-area"

HEART = "❤️"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
