"""
vivastreet/tokens.py — Persistência de tokens JWT por conta VivaStreet.

Os tokens (access_token + refresh_token) são armazenados em
vivastreet_tokens.json no BASE_DIR, indexados pelo account_id.

Padrão idêntico ao persistence.py: leitura/escrita direta em JSON,
sem dependências externas além da stdlib.
"""

import json
import os

from config import BASE_DIR

VIVASTREET_TOKENS_FILE = os.path.join(BASE_DIR, "vivastreet_tokens.json")


def load_tokens(account_id: str) -> dict:
    """Retorna os tokens salvos para o account_id, ou {} se não existirem."""
    if not os.path.exists(VIVASTREET_TOKENS_FILE):
        return {}
    try:
        with open(VIVASTREET_TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(account_id, {})
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def save_tokens(account_id: str, tokens: dict) -> None:
    """Salva (ou sobrescreve) os tokens de um account_id específico."""
    data: dict = {}
    if os.path.exists(VIVASTREET_TOKENS_FILE):
        try:
            with open(VIVASTREET_TOKENS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, TypeError):
            data = {}

    data[account_id] = tokens

    with open(VIVASTREET_TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def clear_tokens(account_id: str) -> None:
    """Remove os tokens de um account_id do arquivo de persistência."""
    save_tokens(account_id, {})


def load_all_tokens() -> dict:
    """Retorna o dicionário completo {account_id: {access_token, refresh_token}}."""
    if not os.path.exists(VIVASTREET_TOKENS_FILE):
        return {}
    try:
        with open(VIVASTREET_TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Persistencia de cookies do browser (Playwright -> requests.Session)
# ---------------------------------------------------------------------------

import os as _os
VIVASTREET_COOKIES_FILE = _os.path.join(BASE_DIR, "vivastreet_cookies.json")


def save_cookies(account_id: str, cookies: list) -> None:
    """
    Salva a lista de cookies retornada por context.cookies() do Playwright.
    Indexada por account_id em vivastreet_cookies.json.
    """
    data: dict = {}
    if _os.path.exists(VIVASTREET_COOKIES_FILE):
        try:
            with open(VIVASTREET_COOKIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, TypeError):
            data = {}
    data[account_id] = cookies
    with open(VIVASTREET_COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_cookies(account_id: str) -> list:
    """Retorna a lista de cookies salvos para o account_id, ou [] se nao existirem."""
    if not _os.path.exists(VIVASTREET_COOKIES_FILE):
        return []
    try:
        with open(VIVASTREET_COOKIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(account_id, [])
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def clear_cookies(account_id: str) -> None:
    """Remove os cookies de um account_id do arquivo de persistencia."""
    save_cookies(account_id, [])
