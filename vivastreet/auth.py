"""
vivastreet/auth.py — Serviço de autenticação da API VivaStreet.

Responsabilidades:
  - Login via Playwright headless (necessário para resolver o Cloudflare Turnstile)
  - Verificação de expiração do JWT (sem biblioteca externa)
  - Refresh automático de access_token via HTTP direto (não precisa de Turnstile)
  - Re-login automático quando o refresh_token expira
  - Persistência segura de tokens via vivastreet/tokens.py
  - Bootstrap de múltiplas contas em paralelo no startup do app

Por que Playwright?
  O endpoint POST /api/v1/auth/user/login exige um "turnstile_token" válido
  (Cloudflare Turnstile). Esse token é gerado por um widget JavaScript que
  detecta automação e bloqueia requests diretas. A solução é usar um browser
  headless real (Playwright/Chromium) para abrir a página de login, preencher
  as credenciais e deixar o widget resolver-se automaticamente. A resposta da
  API é capturada via interceptação de rede.

  O refresh_token não passa pelo Turnstile → usa HTTP direto (requests.Session).

Padrão de threading idêntico ao PlaywrightSession:
  - Métodos públicos disparam threads com daemon=True
  - Comunicação via callbacks: status_cb(msg) e done_cb(success, message, tokens)
"""

import base64
import json
import os
import threading
import time
import traceback
from typing import Callable, Optional

import requests

from config import (
    VIVASTREET_LOGIN_URL,
    VIVASTREET_API_LOGIN_URL,
    VIVASTREET_API_REFRESH_URL,
    USER_AGENT,
    BASE_DIR,
)
from utils import ts
from vivastreet.tokens import load_tokens, save_tokens, clear_tokens


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_TOKEN_BUFFER       = 60      # segundos de margem antes de expirar
_LOGIN_TIMEOUT_MS   = 30_000  # timeout de navegação Playwright (ms)
_RESPONSE_POLL_S    = 1.0     # intervalo de polling para resposta da API
_RESPONSE_MAX_S     = 300     # tempo máximo aguardando login manual (5 minutos)

# Perfis de browser persistidos por conta (cookies + localStorage entre sessões)
_PROFILES_DIR = os.path.join(BASE_DIR, "vivastreet_profiles")


def _find_browser_exe() -> Optional[str]:
    """
    Encontra o executável de um navegador baseado em Chromium instalado no sistema.

    Ordem de preferência: Opera > Opera GX > Edge > Chrome > (None = Chromium do Playwright).

    Opera costuma instalar numa subpasta versionada (ex: Opera/90.0.4480.84/opera.exe),
    por isso a busca usa glob recursivo nas pastas conhecidas do Opera.
    """
    import glob

    local   = os.environ.get("LOCALAPPDATA", "")
    prog    = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    prog86  = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")

    # ── Opera: busca recursiva (instalação versionada) ──────────────────
    opera_dirs = [
        os.path.join(local,  "Programs", "Opera"),
        os.path.join(local,  "Programs", "Opera GX"),
        os.path.join(prog,   "Opera"),
        os.path.join(prog86, "Opera"),
    ]
    for folder in opera_dirs:
        if os.path.isdir(folder):
            # Procura opera.exe em qualquer subpasta (ex: versão numérica)
            hits = glob.glob(os.path.join(folder, "**", "opera.exe"), recursive=True)
            # Exclui installers/uninstallers — pega só o binário principal
            hits = [h for h in hits if "installer" not in h.lower()]
            if hits:
                # Se houver múltiplas versões, pega a mais recente (maior caminho numérico)
                hits.sort(reverse=True)
                return hits[0]

    # ── Fallbacks: Edge → Chrome ─────────────────────────────────────────
    direct_candidates = [
        os.path.join(prog86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(prog,   "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(local,  "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(prog,   "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in direct_candidates:
        if path and os.path.isfile(path):
            return path

    return None  # Playwright usa seu Chromium bundled

_REFRESH_HEADERS = {
    "Content-Type":    "application/json",
    "Accept":          "application/json",
    "Referer":         "https://www.vivastreet.co.uk/",
    "Origin":          "https://www.vivastreet.co.uk",
    "User-Agent":      USER_AGENT,
    "Accept-Language": "en-GB,en;q=0.9",
}

# Script injetado antes do carregamento da página para remover sinais de automação
_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = { runtime: {} };
"""


# ---------------------------------------------------------------------------
# Utilitários JWT (stdlib apenas, sem dependência de pyjwt)
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict:
    """Decodifica o payload de um JWT sem verificar assinatura."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded  = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except Exception:
        return {}


def _is_expired(token: str, buffer: int = _TOKEN_BUFFER) -> bool:
    """Retorna True se o token expirou ou vai expirar em menos de `buffer` segundos."""
    if not token:
        return True
    exp = _decode_jwt_payload(token).get("exp")
    if not exp:
        return True
    return time.time() >= (exp - buffer)


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class VivaStreetAuth:
    """
    Gerencia a autenticação de uma única conta VivaStreet.

    Cada instância é ligada a um account_id (UUID da conta no AccountManager).
    Operações de rede rodam em threads separadas para não bloquear a UI.
    """

    def __init__(self, account_id: str):
        self.account_id   = account_id
        self._http        = requests.Session()
        self._http.headers.update(_REFRESH_HEADERS)
        self._lock        = threading.Lock()

    # ------------------------------------------------------------------
    # API pública (disparam threads e retornam imediatamente)
    # ------------------------------------------------------------------

    def login(
        self,
        username: str,
        password: str,
        status_cb: Optional[Callable[[str], None]] = None,
        done_cb:   Optional[Callable[[bool, str, dict], None]] = None,
    ) -> None:
        """
        Força um novo login via Playwright headless.

        done_cb(success, message, tokens)
          tokens = {"access_token": "...", "refresh_token": "..."}
        """
        threading.Thread(
            target=self._login,
            args=(username, password, status_cb, done_cb),
            daemon=True,
        ).start()

    def ensure_authenticated(
        self,
        username: str,
        password: str,
        status_cb: Optional[Callable[[str], None]] = None,
        done_cb:   Optional[Callable[[bool, str, dict], None]] = None,
    ) -> None:
        """
        Garante sessão válida, escolhendo automaticamente a ação necessária:

        1. access_token válido → retorna sem rede
        2. access_token expirado + refresh válido → faz HTTP refresh
        3. Ambos expirados/ausentes → login via Playwright
        """
        threading.Thread(
            target=self._ensure_authenticated,
            args=(username, password, status_cb, done_cb),
            daemon=True,
        ).start()

    def get_access_token(self) -> Optional[str]:
        """Retorna o access_token atual se válido, sem fazer rede."""
        tokens = load_tokens(self.account_id)
        access = tokens.get("access_token", "")
        return access if access and not _is_expired(access) else None

    def clear(self) -> None:
        """Remove os tokens desta conta do armazenamento persistente."""
        clear_tokens(self.account_id)

    # ------------------------------------------------------------------
    # Implementação interna
    # ------------------------------------------------------------------

    def _log(self, label: str, msg: str) -> None:
        print(f"{ts()} [VS:{label}:{self.account_id[:8]}] {msg}", flush=True)

    # ── Login via Playwright ───────────────────────────────────────────

    def _login(
        self,
        username: str,
        password: str,
        status_cb,
        done_cb,
    ) -> None:
        """
        Abre um browser com perfil PERSISTIDO por conta (cookies + localStorage
        sobrevivem entre sessões).

        Fluxo:
          1. Abre com o perfil salvo em vivastreet_profiles/{account_id}/
          2. Tenta extrair tokens do localStorage (sessão já ativa da última vez)
          3. Se não há tokens válidos, navega para o login:
             - Pré-preenche email e senha
             - Aguarda até 5 min para o usuário resolver CF challenge e fazer login
             - Intercepta a resposta da API para capturar os tokens
          4. Salva tokens em vivastreet_tokens.json e fecha o browser

        Com perfil persistido:
          - 1ª vez: CF challenge aparece → usuário resolve uma vez → perfil salvo
          - Próximas vezes: CF clearance cookie válido → sem challenge
          - Sessão VivaStreet salva no localStorage → pode nem precisar de login
        """
        from playwright.sync_api import sync_playwright

        def s(msg: str) -> None:
            self._log("LOGIN", msg)
            if status_cb:
                status_cb(msg)

        profile_path = os.path.join(_PROFILES_DIR, self.account_id)
        os.makedirs(profile_path, exist_ok=True)

        # Detecta o navegador real instalado (Opera, Edge, Chrome…)
        browser_exe = _find_browser_exe()
        browser_name = (
            os.path.splitext(os.path.basename(browser_exe))[0].title()
            if browser_exe else "Chromium"
        )

        pw = context = None
        try:
            s(f"Abrindo {browser_name} com perfil salvo…")
            pw = sync_playwright().start()

            # Quando usar o browser real do usuário não sobrescrevemos o user-agent —
            # o UA nativo do Opera/Edge/Chrome é reconhecido pelo Cloudflare como
            # um navegador legítimo.  Só definimos UA explícito para o Chromium bundled.
            launch_kwargs: dict = dict(
                user_data_dir=profile_path,
                executable_path=browser_exe,   # None → Chromium bundled do Playwright
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                viewport={"width": 1024, "height": 768},
                locale="en-GB",
                extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
            )
            if browser_exe is None:
                # Somente para Chromium bundled: define UA para evitar fingerprint óbvio
                launch_kwargs["user_agent"] = USER_AGENT

            context = pw.chromium.launch_persistent_context(**launch_kwargs)
            context.add_init_script(_STEALTH_JS)
            page = context.new_page()

            # ── Intercepta qualquer resposta do endpoint de login ─────
            captured: dict = {}

            def on_response(response):
                if VIVASTREET_API_LOGIN_URL in response.url and response.status == 200:
                    try:
                        data = response.json()
                        if data.get("access_token"):
                            captured.update(data)
                    except Exception:
                        pass

            page.on("response", on_response)

            # ── Tenta reutilizar sessão salva no localStorage ──────────
            s("Verificando sessão salva…")
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
                    s("✅ Sessão salva reutilizada com sucesso.")
                    if done_cb:
                        done_cb(True, "Sessão VivaStreet ativa (perfil salvo).", tokens)
                    return
            except Exception:
                pass  # Sessão expirada ou inexistente — prossegue para login

            # ── Login manual ──────────────────────────────────────────
            s("Sessão expirada — abrindo formulário de login…")
            page.goto(
                VIVASTREET_LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=_LOGIN_TIMEOUT_MS,
            )

            # Aguarda o formulário aparecer (pode haver CF challenge antes)
            s("Aguardando formulário de login… (resolva o desafio Cloudflare se aparecer)")
            try:
                page.wait_for_selector(
                    'input[type="email"], input[name="email"], input[autocomplete="username"]',
                    timeout=_RESPONSE_MAX_S * 1000,
                )
            except Exception:
                debug_path = os.path.join(BASE_DIR, "vs_login_debug.png")
                try:
                    page.screenshot(path=debug_path)
                    s(f"⚠️  Screenshot salvo em: {debug_path}")
                except Exception:
                    pass
                raise RuntimeError("Formulário de login não encontrado após timeout.")

            # Pré-preenche credenciais (o usuário só precisa resolver o Turnstile / clicar Login)
            try:
                page.fill('input[type="email"]',    username)
                page.fill('input[type="password"]', password)
                s("Credenciais preenchidas. Clique em 'Login' na janela se necessário.")
            except Exception:
                s("⚠️  Não foi possível pré-preencher credenciais — preencha manualmente.")

            # Aguarda a resposta do login OU tokens no localStorage
            s(f"Aguardando login completar… (até {_RESPONSE_MAX_S // 60} min)")
            elapsed = 0
            while elapsed < _RESPONSE_MAX_S:
                if captured.get("access_token"):
                    break
                # Verifica localStorage enquanto aguarda
                tokens = self._extract_tokens_from_page(page)
                if tokens:
                    captured.update({
                        "access_token":  tokens["access_token"],
                        "refresh_token": tokens.get("refresh_token", ""),
                    })
                    break
                page.wait_for_timeout(int(_RESPONSE_POLL_S * 1000))
                elapsed += _RESPONSE_POLL_S

            # ── Processa resultado ────────────────────────────────────
            if captured.get("access_token"):
                tokens = {
                    "access_token":  captured["access_token"],
                    "refresh_token": captured.get("refresh_token", ""),
                }
                with self._lock:
                    save_tokens(self.account_id, tokens)
                s("✅ Login VivaStreet realizado. Perfil salvo para próximas sessões.")
                if done_cb:
                    done_cb(True, "Login VivaStreet OK.", tokens)
                return

            msg = f"Login não concluído em {_RESPONSE_MAX_S // 60} minutos."
            s(f"❌ {msg}")
            if done_cb:
                done_cb(False, msg, {})

        except Exception as exc:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"Erro durante login VivaStreet: {exc}", {})

        finally:
            try:
                if context:
                    context.close()
                if pw:
                    pw.stop()
            except Exception:
                pass

    def _extract_tokens_from_page(self, page) -> Optional[dict]:
        """
        Tenta extrair access_token e refresh_token do localStorage da página.
        O app usa pinia-plugin-persistedstate, que salva o store 'auth' no localStorage.
        """
        try:
            # Tenta as chaves mais prováveis do pinia-plugin-persistedstate
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
                // Varre todo o localStorage em busca de accessToken
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

    # ── Refresh via HTTP (sem Turnstile) ──────────────────────────────

    def _refresh(self, refresh_token: str, status_cb) -> Optional[dict]:
        """
        Renova o access_token via HTTP direto (não requer Turnstile).
        Retorna o dicionário de tokens atualizado, ou None em caso de falha.
        Deve ser chamado dentro de uma thread.
        """
        self._log("REFRESH", "Renovando access_token…")
        if status_cb:
            status_cb("VivaStreet: renovando token…")

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

                self._log("REFRESH", "✅ Token renovado.")
                return tokens

            else:
                self._log("REFRESH", f"Refresh falhou (HTTP {resp.status_code}).")
                return None

        except requests.RequestException as exc:
            self._log("REFRESH", f"Erro de rede no refresh: {exc}")
            return None

    # ── Orquestrador ──────────────────────────────────────────────────

    def _ensure_authenticated(
        self,
        username: str,
        password: str,
        status_cb,
        done_cb,
    ) -> None:
        """
        Escolhe a estratégia de autenticação com base no estado dos tokens.
        Roda dentro de uma thread (chamado via ensure_authenticated()).
        """
        tokens  = load_tokens(self.account_id)
        access  = tokens.get("access_token",  "")
        refresh = tokens.get("refresh_token", "")

        # Caso 1: access_token ainda válido
        if access and not _is_expired(access):
            self._log("AUTH", "Sessão ativa — sem ação necessária.")
            if done_cb:
                done_cb(True, "Sessão VivaStreet ativa.", tokens)
            return

        # Caso 2: access expirado, refresh ainda válido → HTTP refresh
        if refresh and not _is_expired(refresh):
            self._log("AUTH", "Access expirado — tentando refresh.")
            new_tokens = self._refresh(refresh, status_cb)
            if new_tokens:
                if done_cb:
                    done_cb(True, "Token VivaStreet renovado.", new_tokens)
                return
            self._log("AUTH", "Refresh falhou — realizando novo login via browser.")

        # Caso 3: ambos expirados/ausentes → login via Playwright
        self._login(username, password, status_cb, done_cb)


# ---------------------------------------------------------------------------
# Função de bootstrap (múltiplas contas em paralelo)
# ---------------------------------------------------------------------------

def bootstrap_vivastreet_accounts(
    accounts: list,
    status_cb: Optional[Callable[[str], None]] = None,
    done_cb:   Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Autentica todas as contas VivaStreet em paralelo ao iniciar o app.

    Parâmetros:
        accounts  — lista de objetos Account com type == "vivastreet"
        status_cb — mensagens de progresso (será chamado de threads, use self.after)
        done_cb   — chamado ao final com (n_ok, n_fail) quando todas terminarem
    """
    if not accounts:
        if done_cb:
            done_cb(0, 0)
        return

    total   = len(accounts)
    results = {"ok": 0, "fail": 0, "pending": total}
    lock    = threading.Lock()

    def _on_account_done(success: bool, _message: str, _tokens: dict) -> None:
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
