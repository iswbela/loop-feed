"""
accounts.py — Gerenciamento de contas de serviços externos.

Suporta contas do tipo "kommons" e "vivastreet".
As contas são persistidas localmente em accounts.json.
"""

import json
import os
import uuid
from dataclasses import dataclass, asdict
from typing import List, Optional

from config import BASE_DIR

ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")

ACCOUNT_TYPE_KOMMONS = "kommons"
ACCOUNT_TYPE_VIVASTREET = "vivastreet"

ACCOUNT_TYPES = [ACCOUNT_TYPE_KOMMONS, ACCOUNT_TYPE_VIVASTREET]

ACCOUNT_TYPE_LABELS = {
    ACCOUNT_TYPE_KOMMONS: "Kommons",
    ACCOUNT_TYPE_VIVASTREET: "VivaStreet",
}


@dataclass
class Account:
    id: str
    type: str          # "kommons" | "vivastreet"
    username: str
    password: str
    label: str = ""    # nome de exibição opcional

    def display_name(self) -> str:
        return self.label if self.label else self.username

    def type_label(self) -> str:
        return ACCOUNT_TYPE_LABELS.get(self.type, self.type.capitalize())


class AccountManager:
    """Carrega e persiste contas de serviços externos."""

    # ---------- leitura / escrita ----------

    def load(self) -> List[Account]:
        if not os.path.exists(ACCOUNTS_FILE):
            return []
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Account(**item) for item in data]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def save(self, accounts: List[Account]) -> None:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump([asdict(a) for a in accounts], f, indent=2, ensure_ascii=False)

    # ---------- operações CRUD ----------

    def add(self, account: Account) -> None:
        accounts = self.load()
        accounts.append(account)
        self.save(accounts)

    def update(self, account: Account) -> None:
        accounts = self.load()
        for i, a in enumerate(accounts):
            if a.id == account.id:
                accounts[i] = account
                break
        self.save(accounts)

    def remove(self, account_id: str) -> None:
        accounts = self.load()
        accounts = [a for a in accounts if a.id != account_id]
        self.save(accounts)

    def get_by_id(self, account_id: str) -> Optional[Account]:
        for a in self.load():
            if a.id == account_id:
                return a
        return None

    def list_by_type(self, account_type: str) -> List[Account]:
        return [a for a in self.load() if a.type == account_type]

    # ---------- utilitário ----------

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())
