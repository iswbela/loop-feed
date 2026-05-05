"""
browser/__init__.py — Sessão HTTP para o Kommons.

KommonsSession encapsula todo o ciclo de vida de autenticação e
edição de anúncios via requisições HTTP (sem browser headless).

Interface pública mantida compatível com o pw_session já esperado
pelo EditWindow:
    session.update(edit_url, fn_or_text, status_cb, done_cb)
    session.get_ads() -> list[dict]
    session.logged_in   -> bool
"""

from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional, Union

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, LOGIN_URL, DASHBOARD_URL, USER_AGENT

# ---------------------------------------------------------------------------
# Cabeçalhos padrão
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ---------------------------------------------------------------------------
# Helpers de scraping
# ---------------------------------------------------------------------------

def _extract_csrf(html: str) -> Optional[str]:
    """Extrai o token CSRF (campo 'grt') do formulário de login."""
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.find("input", {"name": "grt"})
    if inp:
        val = inp.get("value", "")
        return val if val else None
    return None


def _is_logged_in_page(html: str) -> bool:
    """Heurística: página autenticada não deve conter o formulário de login."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("input", {"name": "grt"}) is None and (
        soup.find("a", href=re.compile(r"/members-area/logout")) is not None
        or soup.find(class_=re.compile(r"logout|dashboard|members", re.I)) is not None
    )


def _parse_ads(html: str) -> List[dict]:
    """
    Extrai a lista de anúncios do dashboard do Kommons.

    Retorna lista de dicts com as chaves:
        name, escort_id, edit_url, image_url
    """
    soup = BeautifulSoup(html, "html.parser")
    ads: List[dict] = []

    # Tenta padrão 1: links para /members-area/ad/
    for link in soup.find_all("a", href=re.compile(r"/members-area/ad/")):
        href = link.get("href", "")
        # Extrai o ID do anúncio (último segmento da URL)
        parts = [p for p in href.split("/") if p]
        if not parts:
            continue
        escort_id = parts[-1]

        # Nome: texto do link ou alt da imagem dentro dele
        img = link.find("img")
        name = ""
        if img:
            name = img.get("alt", "").strip()
        if not name:
            name = link.get_text(strip=True) or escort_id

        # Imagem de thumbnail
        image_url = ""
        if img:
            src = img.get("src") or img.get("data-src", "")
            if src and not src.startswith("data:"):
                image_url = src if src.startswith("http") else BASE_URL + src

        edit_url = href if href.startswith("http") else BASE_URL + href

        # Evita duplicatas
        if not any(a["escort_id"] == escort_id for a in ads):
            ads.append(
                {
                    "name": name,
                    "escort_id": escort_id,
                    "edit_url": edit_url,
                    "image_url": image_url,
                }
            )

    return ads


def _parse_description(html: str) -> str:
    """Extrai o valor atual do campo 'description' na página de edição."""
    soup = BeautifulSoup(html, "html.parser")

    # Tenta <textarea name="description">
    ta = soup.find("textarea", {"name": "description"})
    if ta:
        return ta.get_text()

    # Tenta <input name="description">
    inp = soup.find("input", {"name": "description"})
    if inp:
        return inp.get("value", "")

    return ""


def _extract_form_fields(html: str, exclude: tuple = ()) -> dict:
    """Extrai todos os campos hidden de um formulário (CSRF, etc.)."""
    soup = BeautifulSoup(html, "html.parser")
    fields: dict = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        if name and name not in exclude:
            fields[name] = inp.get("value", "")
    return fields


# ---------------------------------------------------------------------------
# Sessão principal
# ---------------------------------------------------------------------------

class KommonsSession:
    """
    Gerencia autenticação e operações sobre anúncios do Kommons via HTTP.

    Uso:
        session = KommonsSession(username="email", password="senha")
        ok = session.login()
        ads = session.get_ads()
        session.update(edit_url, simple_toggle, status_cb, done_cb)
    """

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.logged_in = False
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """
        Realiza login no Kommons.
        Retorna True em caso de sucesso, False caso contrário.
        """
        with self._lock:
            try:
                # 1. Visita a home para obter cookies iniciais
                self._session.get(BASE_URL, timeout=15)

                # 2. Carrega a página de login para extrair token CSRF
                r = self._session.get(
                    LOGIN_URL,
                    headers={"Referer": BASE_URL},
                    timeout=15,
                )
                token = _extract_csrf(r.text)

                payload: dict = {"user": self.username, "password": self.password}
                if token:
                    payload["grt"] = token

                # 3. Envia credenciais
                resp = self._session.post(
                    LOGIN_URL,
                    data=payload,
                    headers={"Referer": LOGIN_URL},
                    timeout=15,
                    allow_redirects=True,
                )

                # 4. Verifica se o login foi bem-sucedido
                self.logged_in = _is_logged_in_page(resp.text) or (
                    DASHBOARD_URL in resp.url
                    and "login" not in resp.url
                )
                return self.logged_in

            except requests.RequestException:
                self.logged_in = False
                return False

    def logout(self) -> None:
        try:
            self._session.get(f"{BASE_URL}/members-area/logout", timeout=10)
        except requests.RequestException:
            pass
        finally:
            self.logged_in = False

    # ------------------------------------------------------------------
    # Busca de anúncios
    # ------------------------------------------------------------------

    def get_ads(self) -> List[dict]:
        """
        Retorna a lista de anúncios do usuário.
        Requer que login() tenha sido chamado com sucesso.
        """
        with self._lock:
            try:
                r = self._session.get(
                    DASHBOARD_URL,
                    headers={"Referer": BASE_URL},
                    timeout=15,
                )
                ads = _parse_ads(r.text)

                # Fallback: tenta /members-area/ads
                if not ads:
                    r2 = self._session.get(
                        f"{BASE_URL}/members-area/ads",
                        headers={"Referer": DASHBOARD_URL},
                        timeout=15,
                    )
                    ads = _parse_ads(r2.text)

                return ads
            except requests.RequestException:
                return []

    # ------------------------------------------------------------------
    # Atualização de descrição
    # ------------------------------------------------------------------

    def update(
        self,
        edit_url: str,
        fn_or_text: Union[Callable[[str], str], str],
        status_cb: Optional[Callable[[str], None]] = None,
        done_cb: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        """
        Atualiza a descrição de um anúncio em background thread.

        Args:
            edit_url:    URL da página de edição do anúncio.
            fn_or_text:  Callable que recebe a descrição atual e retorna a
                         nova descrição, OU uma string que substitui direto.
            status_cb:   Chamado com mensagens de progresso (thread-safe via after).
            done_cb:     Chamado com (success: bool, message: str) ao terminar.
        """

        def _run():
            def _status(msg: str):
                if status_cb:
                    status_cb(msg)

            try:
                _status("Carregando página do anúncio…")

                with self._lock:
                    r = self._session.get(
                        edit_url,
                        headers={"Referer": DASHBOARD_URL},
                        timeout=15,
                    )

                # Determina nova descrição
                if callable(fn_or_text):
                    current_desc = _parse_description(r.text)
                    new_desc = fn_or_text(current_desc)
                else:
                    new_desc = fn_or_text

                # Coleta campos hidden do formulário (CSRF, etc.)
                hidden = _extract_form_fields(r.text, exclude=("description",))

                _status("Enviando nova descrição…")

                payload = {**hidden, "description": new_desc}

                # Determina action do formulário
                soup = BeautifulSoup(r.text, "html.parser")
                form = soup.find("form")
                action = edit_url
                if form:
                    act = form.get("action", "")
                    if act:
                        action = act if act.startswith("http") else BASE_URL + act

                with self._lock:
                    resp = self._session.post(
                        action,
                        data=payload,
                        headers={"Referer": edit_url},
                        timeout=15,
                        allow_redirects=True,
                    )

                # Verifica se foi bem-sucedido (heurística)
                resp_soup = BeautifulSoup(resp.text, "html.parser")
                ok = (
                    resp.status_code < 400
                    and resp_soup.find(
                        string=re.compile(r"success|saved|atualiz|salvo", re.I)
                    ) is not None
                ) or resp.status_code in (200, 302)

                if done_cb:
                    if ok:
                        done_cb(True, "✅ Descrição atualizada com sucesso!")
                    else:
                        done_cb(False, f"⚠️ Resposta inesperada do servidor (HTTP {resp.status_code}).")

            except requests.RequestException as exc:
                if done_cb:
                    done_cb(False, f"❌ Erro de rede: {exc}")
            except Exception as exc:
                if done_cb:
                    done_cb(False, f"❌ Erro inesperado: {exc}")

        threading.Thread(target=_run, daemon=True).start()
