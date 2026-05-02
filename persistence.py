import json
import os

from config import CONFIG_FILE, MESSAGES_FILE


def save_credentials(user: str, password: str) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"user": user, "password": password}, f, indent=2)


def load_credentials() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_messages() -> dict:
    if os.path.exists(MESSAGES_FILE):
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_messages(data: dict) -> None:
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_messages_for(escort_id: str) -> list[str]:
    return load_messages().get(escort_id, [])


def set_messages_for(escort_id: str, msgs: list[str]) -> None:
    data = load_messages()
    data[escort_id] = msgs
    save_messages(data)
