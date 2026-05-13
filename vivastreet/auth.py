"""
vivastreet/auth.py — Servico de autenticacao da API VivaStreet.

Responsabilidades:
  - Login via browser real conectado via CDP (evita detecção Cloudflare)
  - Fallback para launchPersistentContext com perfil salvo se CDP indisponível
  - Verificacao de expiracao do JWT (sem biblioteca externa)
  - Refresh automatico de access_token via HTTP direto
  - Re-login automatico quando o refresh_token expira
  - Persistencia segura de tokens via vivastreet/tokens.py
  - Persistencia de cookies do browser para uso em requisicoes HTTP diretas
  - Bootstrap de multiplas contas em paralelo no startup do app

Detecção de browser: usa browser/launcher.py (Chrome > Opera > Opera GX).
Perfil persistente:  %APPDATA%\\LoopFeed\\BrowserProfile (compartilhado com CDP).
"""

import base64
import json
import threading
import time
import traceback
from typing import Callable, Optional

import requests

from browser.launcher import (
    ensure_browser_with_cdp,
    cdp_info,
    CDP_ENDPOINT,
    APP_PROFILE_DIR,
    detect_browser,
    launch_with_cdp,
    wait_for_cdp,
)
from config import (
    VIVASTREET_LOGIN_URL,
    VIVASTREET_API_LOGIN_URL,
    VIVASTREET_API_REFRESH_URL,
    USER_AGENT,
)
from utils import ts
from vivastreet.tokens import load_tokens, save_tokens, clear_tokens, save_cookies

_TOKEN_BUFFER       = 60
_LOGIN_TIMEOUT_MS   = 30_000
_RESPONSE_POLL_S    = 1.0
_RESPONSE_MAX_S     = 300

_REFRESH_HEADERS = {
    "Content-Type":    "application/json",
    "Accept":          "application/json",
    "Referer":         "https://www.vivastreet.co.uk/",
    "Origin":          "https://www.vivastreet.co.uk",
    "User-Agent":      USER_AGENT,
    "Accept-Language": "en-GB,en;q=0.9",
}

_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = { runtime: {} };
"""


def _decode_jwt_payload(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded  = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except Exception:
        return {}


def _is_expired(token, buffer=60):
    if not token:
        return True
    exp = _decode_jwt_payload(token).get("exp")
    if not exp:
        return True
    return time.time() >= (exp - buffer)


def _save_context_cookies(account_id, context, log_fn):
    """
    Extrai todos os cookies do contexto Playwright e salva em disco.
    Esses cookies (especialmente cf_clearance) sao necessarios para que
    requisicoes HTTP diretas passem pela protecao do Cloudflare.
    """
    try:
        raw_cookies = context.cookies()
        save_cookies(account_id, raw_cookies)
        cf_count = sum(1 for c in raw_cookies if "cf_" in c.get("name", ""))
        log_fn(
            f"cookie {len(raw_cookies)} cookie(s) salvos para requisicoes HTTP "
            f"({cf_count} Cloudflare)."
        )
    except Exception as exc:
        log_fn(f"Nao foi possivel salvar cookies: {exc}")


class VivaStreetAuth:
    def __init__(self, account_id):
        self.account_id = account_id
        self._http = requests.Session()
        self._http.headers.update(_REFRESH_HEADERS)
        self._lock = threading.Lock()

    def login(self, username, password, status_cb=None, done_cb=None):
        threading.Thread(
            target=self._login,
            args=(username, password, status_cb, done_cb),
            daemon=True,
        ).start()

    def ensure_authenticated(self, username, password, status_cb=None, done_cb=None):
        threading.Thread(
            target=self._ensure_authenticated,
            args=(username, password, status_cb, done_cb),
            daemon=True,
        ).start()

    def get_access_token(self):
        tokens = load_tokens(self.account_id)
        access = tokens.get("access_token", "")
        return access if access and not _is_expired(access) else None

    def clear(self):
        clear_tokens(self.account_id)

    def _log(self, label, msg):
        print(f"{ts()} [VS:{label}:{self.account_id[:8]}] {msg}", flush=True)

    def _login(self, username, password, status_cb, done_cb):
        from playwright.sync_api import sync_playwright

        def s(msg):
            self._log("LOGIN", msg)
            if status_cb:
                status_cb(msg)

        pw       = None
        context  = None
        page     = None
        cdp_brow = None
        used_cdp = False

        try:
            pw = sync_playwright().start()

            # ── Opção A: garantir browser rodando com CDP e conectar ──────
            # ensure_browser_with_cdp() detecta Chrome/Opera/Opera GX,
            # mata instâncias existentes se necessário, inicia com
            # --remote-debugging-port=9222 e --user-data-dir persistente,
            # e aguarda o CDP ficar disponível.
            ok, msg = ensure_browser_with_cdp(status_cb=s)
            if ok:
                try:
                    s(f"Conectando ao browser via CDP ({CDP_ENDPOINT})...")
                    cdp_brow = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=5000)
                    contexts = cdp_brow.contexts
                    context  = contexts[0] if contexts else cdp_brow.new_context()
                    page     = context.new_page()
                    used_cdp = True
                    s("Conectado ao browser real — abrindo nova aba.")
                except Exception as cdp_exc:
                    s(f"Falha ao conectar via CDP ({cdp_exc.__class__.__name__}) — usando fallback.")
                    used_cdp = False
                    cdp_brow = None

            # ── Fallback: launchPersistentContext com perfil próprio ──────
            if not used_cdp:
                _, browser_exe = detect_browser()
                profile_path   = str(APP_PROFILE_DIR)

                browser_name = "Chromium"
                if browser_exe:
                    import os
                    browser_name = os.path.splitext(os.path.basename(browser_exe))[0].title()

                s(f"Abrindo {browser_name} com perfil persistente...")

                launch_kwargs = dict(
                    user_data_dir=profile_path,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    viewport={"width": 1024, "height": 768},
                    locale="en-GB",
                    extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
                )
                if browser_exe:
                    launch_kwargs["executable_path"] = browser_exe

                context = pw.chromium.launch_persistent_context(**launch_kwargs)
                context.add_init_script(_STEALTH_JS)
                page = context.new_page()

            captured = {}

            def on_response(response):
                try:
                    if VIVASTREET_API_LOGIN_URL in response.url and response.status == 200:
                        data = response.json()
                        if data.get("access_token"):
                            captured.update(data)
                except Exception:
                    pass

            page.on("response", on_response)

            # Tenta reutilizar sessao salva no localStorage / perfil persistente
            s("Verificando sessao salva...")
            try:
                page.goto(
                    f"{VIVASTREET_LOGIN_URL.rsplit('/user', 1)[0]}/user/account/ads",
                    wait_until="domcontentloaded",
                    timeout=_LOGIN_TIMEOUT_MS,
                )
                page.wait_for_load_state("load", timeout=_LOGIN_TIMEOUT_MS)
                tokens = self._extract_tokens_from_page(page)
                if tokens:
                    with self._lock:
                        save_tokens(self.account_id, tokens)
                        _save_context_cookies(self.account_id, context, s)
                    s("Sessao salva reutilizada com sucesso.")
                    if done_cb:
                        done_cb(True, "Sessao VivaStreet ativa (perfil salvo).", tokens)
                    return
            except Exception:
                pass

            # Login manual via formulário
            s("Sessao expirada -- abrindo formulario de login...")
            page.goto(VIVASTREET_LOGIN_URL, wait_until="domcontentloaded", timeout=_LOGIN_TIMEOUT_MS)

            # Aguarda o campo de email aparecer — funciona tanto na chegada direta
            # quanto após CAPTCHA (o formulário só aparece depois que o CAPTCHA é resolvido)
            s("Aguardando campo de email ficar visivel (resolve o CAPTCHA se aparecer)...")
            try:
                page.wait_for_selector('#email', state='visible', timeout=_RESPONSE_MAX_S * 1000)
            except Exception:
                s("Campo de email nao apareceu no tempo limite.")

            # Pausa de 3s depois que o formulario aparece, para garantir que o Vue carregou
            s("Formulario visivel. Aguardando 3s para estabilizar...")
            page.wait_for_timeout(3000)

            try:
                email_loc = page.locator('#email')
                pw_loc    = page.locator('#current-password')

                # Preenche e verifica — tenta ate 3 vezes caso os campos sumam
                for tentativa in range(1, 4):
                    s(f"Preenchendo credenciais (tentativa {tentativa}/3)...")
                    email_loc.fill(username)
                    pw_loc.fill(password)
                    page.wait_for_timeout(500)

                    email_val = email_loc.input_value()
                    pw_val    = pw_loc.input_value()

                    if email_val and pw_val:
                        s(f"Campos confirmados (email: {len(email_val)} chars, senha: {len(pw_val)} chars).")
                        break
                    s(f"Campos vazios apos tentativa {tentativa} — tentando novamente...")
                else:
                    s("Nao foi possivel manter os campos preenchidos — tentando login assim mesmo.")

                s("Clicando em Login...")
                page.click('button[data-automation="urD_Login"]')
                s("Botao de login clicado. Aguardando autenticacao...")
            except Exception as click_exc:
                s(f"Erro ao preencher/clicar ({click_exc.__class__.__name__}) -- preencha manualmente.")

            s(f"Aguardando login completar... (ate {_RESPONSE_MAX_S // 60} min)")
            elapsed = 0
            while elapsed < _RESPONSE_MAX_S:
                if captured.get("access_token"):
                    break
                tokens = self._extract_tokens_from_page(page)
                if tokens:
                    captured.update({
                        "access_token":  tokens["access_token"],
                        "refresh_token": tokens.get("refresh_token", ""),
                    })
                    break
                page.wait_for_timeout(int(_RESPONSE_POLL_S * 1000))
                elapsed += _RESPONSE_POLL_S

            if captured.get("access_token"):
                tokens = {
                    "access_token":  captured["access_token"],
                    "refresh_token": captured.get("refresh_token", ""),
                }
                with self._lock:
                    save_tokens(self.account_id, tokens)
                    _save_context_cookies(self.account_id, context, s)
                s("Login VivaStreet realizado. Perfil salvo para proximas sessoes.")
                if done_cb:
                    done_cb(True, "Login VivaStreet OK.", tokens)
                return

            msg = f"Login nao concluido em {_RESPONSE_MAX_S // 60} minutos."
            s(f"ERRO: {msg}")
            if done_cb:
                done_cb(False, msg, {})

        except Exception as exc:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"Erro durante login VivaStreet: {exc}", {})
        finally:
            try:
                if used_cdp:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
                    if cdp_brow:
                        try:
                            cdp_brow.close()
                        except Exception:
                            pass
                else:
                    if context:
                        context.close()
                if pw:
                    pw.stop()
            except Exception:
                pass

    def _extract_tokens_from_page(self, page):
        try:
            result = page.evaluate("""() => {
                const keys = ['auth', 'pinia-auth', 'vivastreet-auth', 'user-auth'];
                for (const key of keys) {
                    try {
                        const raw = localStorage.getItem(key);
                        if (!raw) continue;
                        const data = JSON.parse(raw);
                        const at = data.accessToken || data.access_token;
                        const rt = data.refreshToken || data.refresh_token;
                        if (at) return { access_token: at, refresh_token: rt || '' };
                    } catch {}
                }
                for (let i = 0; i < localStorage.length; i++) {
                    try {
                        const raw = localStorage.getItem(localStorage.key(i));
                        const data = JSON.parse(raw);
                        const at = data.accessToken || data.access_token;
                        const rt = data.refreshToken || data.refresh_token;
                        if (at) return { access_token: at, refresh_token: rt || '' };
                    } catch {}
                }
                return null;
            }""")
            if result and result.get("access_token"):
                return result
        except Exception:
            pass
        return None

    def _refresh(self, refresh_token, status_cb):
        self._log("REFRESH", "Renovando access_token...")
        if status_cb:
            status_cb("VivaStreet: renovando token...")
        try:
            resp = self._http.post(
                VIVASTREET_API_REFRESH_URL,
                json={"refresh_token": refresh_token},
                timeout=15,
            )
            if resp.status_code == 200:
                data        = resp.json()
                new_access  = data.get("access_token", "")
                new_refresh = data.get("refresh_token", refresh_token)
                if not new_access:
                    self._log("REFRESH", "Resposta sem access_token.")
                    return None
                tokens = {"access_token": new_access, "refresh_token": new_refresh}
                with self._lock:
                    save_tokens(self.account_id, tokens)
                self._log("REFRESH", "Token renovado.")
                return tokens
            else:
                self._log("REFRESH", f"Refresh falhou (HTTP {resp.status_code}).")
                return None
        except requests.RequestException as exc:
            self._log("REFRESH", f"Erro de rede no refresh: {exc}")
            return None

    def _ensure_authenticated(self, username, password, status_cb, done_cb):
        tokens  = load_tokens(self.account_id)
        access  = tokens.get("access_token",  "")
        refresh = tokens.get("refresh_token", "")

        if access and not _is_expired(access):
            self._log("AUTH", "Sessao ativa -- sem acao necessaria.")
            if done_cb:
                done_cb(True, "Sessao VivaStreet ativa.", tokens)
            return

        if refresh and not _is_expired(refresh):
            self._log("AUTH", "Access expirado -- tentando refresh.")
            new_tokens = self._refresh(refresh, status_cb)
            if new_tokens:
                if done_cb:
                    done_cb(True, "Token VivaStreet renovado.", new_tokens)
                return
            self._log("AUTH", "Refresh falhou -- realizando novo login via browser.")

        self._login(username, password, status_cb, done_cb)


def bootstrap_vivastreet_accounts(accounts, status_cb=None, done_cb=None):
    if not accounts:
        if done_cb:
            done_cb(0, 0)
        return

    total   = len(accounts)
    results = {"ok": 0, "fail": 0, "pending": total}
    lock    = threading.Lock()

    def _on_account_done(success, _message, _tokens):
        with lock:
            if success:
                results["ok"]   += 1
            else:
                results["fail"] += 1
            results["pending"] -= 1
            all_done = results["pending"] == 0
        if all_done and done_cb:
            done_cb(results["ok"], results["fail"])

    for acc in accounts:
        VivaStreetAuth(acc.id).ensure_authenticated(
            acc.username,
            acc.password,
            status_cb=status_cb,
            done_cb=_on_account_done,
        )
