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

# User-Agent alinhado a uma versão recente do Chrome estável (Windows desktop).
# Mantenha sincronizado com a maior versão estável do Chromium para reduzir
# discrepância entre UA declarado e fingerprint TLS/JA3. Quando estamos
# conectados via CDP ao Edge/Opera/Chrome real, NÃO sobrescrevemos o UA — o
# binário expõe o seu próprio UA, que é o que a Cloudflare espera ver.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --- Perfis persistentes de navegador (cookies, localStorage, cf_clearance) ---
# Cada conta VivaStreet recebe um subdiretório próprio:
#   PROFILES_DIR/<account_id>/
# Esses perfis sobrevivem a múltiplas execuções: depois que o usuário
# resolve o Cloudflare Turnstile uma vez, o cf_clearance fica salvo aqui
# e o navegador volta a abrir sem desafio.
PROFILES_DIR = os.path.join(BASE_DIR, "vivastreet_profiles")

# --- Estratégia CDP ---
# Ao lançar o navegador real (Edge/Chrome/Opera) como subprocess, abrimos
# uma porta de debug. Cada conta usa uma porta diferente partindo dessa
# base. Usar uma porta fixa por conta evita conflitos quando rodamos o
# bootstrap em paralelo.
CDP_PORT_BASE = 9222
CDP_CONNECT_TIMEOUT_S = 25  # tempo máximo aguardando a porta responder
