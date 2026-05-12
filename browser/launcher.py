"""
browser/launcher.py — Estratégia híbrida de inicialização de browser.

Objetivo:
  Abrir uma sessão de navegador que pareça o mais legítima possível para a
  Cloudflare. A melhor opção é reaproveitar o navegador real que o usuário
  já tem instalado (Edge/Chrome/Opera), porque:

    * O fingerprint TLS/JA3 desse binário corresponde exatamente ao que a
      Cloudflare espera de um navegador real (o cf_clearance fica preso a
      esse fingerprint — qualquer divergência invalida a clearance imediatamente).
    * O navegador real expõe um conjunto de APIs internas, headers e timing
      de rede que o Chromium do Playwright não replica perfeitamente.
    * O cf_clearance e o cookie de sessão do VivaStreet ficam salvos no
      perfil persistido (--user-data-dir) e sobrevivem entre execuções.

Estratégia em duas etapas (híbrida):

  1) CDP — preferida:
     - Localiza o executável de um navegador real instalado.
     - Lança-o como subprocess com:
         --remote-debugging-port=PORT
         --user-data-dir=PROFILES_DIR/<account_id>
         (flags mínimas, sem --no-sandbox; viewport nativo)
     - Aguarda a porta de debug responder.
     - Conecta o Playwright via `chromium.connect_over_cdp(...)`.

  2) Fallback — Playwright launch_persistent_context:
     - Se nenhum navegador real é encontrado, ou a porta CDP não responde,
       cai para o Chromium bundled do Playwright em modo persistente.
     - Aplica stealth abrangente (browser/stealth.py).
     - Usa channel="msedge" ou "chrome" se disponível, para pelo menos
       herdar o binário real (mas sem CDP).

A função `launch_browser()` retorna sempre uma tupla:

    (playwright_instance, browser_context, launched_subprocess_or_none)

O caller é responsável por:
  * fechar `browser_context` (não fecha o subprocess do CDP — o navegador real
    deve sobreviver para preservar a sessão)
  * matar `launched_subprocess_or_none` somente quando QUISER encerrar a sessão
  * chamar `playwright_instance.stop()`

Trade-offs:
  * Conectar via CDP mantém o navegador aberto entre execuções? Não — a função
    encerra o subprocess no `close_all` (porque o usuário não quer interferência
    no Opera principal). O perfil dedicado em PROFILES_DIR/<account_id> garante
    persistência sem usar o perfil padrão do navegador do usuário.
"""

from __future__ import annotations

import glob
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from playwright.sync_api import Playwright, BrowserContext, sync_playwright

from config import (
    PROFILES_DIR,
    CDP_PORT_BASE,
    CDP_CONNECT_TIMEOUT_S,
    USER_AGENT,
)


# ---------------------------------------------------------------------------
# Resultado da função pública
# ---------------------------------------------------------------------------

@dataclass
class LaunchResult:
    pw: Playwright
    context: BrowserContext
    subprocess: Optional[subprocess.Popen]  # None se usou fallback persistent
    mode: str                                # "cdp" ou "persistent"
    browser_name: str                        # "Edge" / "Chrome" / "Opera" / "Chromium"

    def close_all(self) -> None:
        """Fecha o context, mata o subprocess CDP (se houver) e para o Playwright."""
        try:
            self.context.close()
        except Exception:
            pass
        # Damos uma janela curta para o navegador real persistir cookies/storage
        # antes de matar o processo. Sem isso o cf_clearance pode não ser gravado.
        if self.subprocess is not None:
            try:
                self.subprocess.terminate()
                try:
                    self.subprocess.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.subprocess.kill()
            except Exception:
                pass
        try:
            self.pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Localização de binários reais
# ---------------------------------------------------------------------------

def _find_browser_binary() -> Tuple[Optional[str], str]:
    """
    Retorna (caminho_executavel, nome_amigavel) do navegador real preferido.

    Ordem de preferência:
      1. Microsoft Edge — mais estável com CDP, presente em todo Windows 10+.
      2. Google Chrome — segundo mais estável.
      3. Opera Stable / Opera GX — funcionam, porém com patches próprios que
         eventualmente conflitam com CDP. Última opção dentre os reais.

    Se nenhum for encontrado, retorna (None, "Chromium").
    """
    local   = os.environ.get("LOCALAPPDATA", "")
    prog    = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    prog86  = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

    # ── Edge ────────────────────────────────────────────────────────────
    edge_candidates = [
        os.path.join(prog86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(prog,   "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for p in edge_candidates:
        if os.path.isfile(p):
            return p, "Edge"

    # ── Chrome ──────────────────────────────────────────────────────────
    chrome_candidates = [
        os.path.join(prog,   "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local,  "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in chrome_candidates:
        if os.path.isfile(p):
            return p, "Chrome"

    # ── Opera (instalação versionada) ───────────────────────────────────
    opera_roots = [
        os.path.join(local,  "Programs", "Opera"),
        os.path.join(local,  "Programs", "Opera GX"),
        os.path.join(prog,   "Opera"),
        os.path.join(prog86, "Opera"),
    ]
    for root in opera_roots:
        if not os.path.isdir(root):
            continue
        hits = glob.glob(os.path.join(root, "**", "opera.exe"), recursive=True)
        hits = [h for h in hits if "installer" not in h.lower()]
        if hits:
            hits.sort(reverse=True)  # versão mais recente
            return hits[0], "Opera"

    return None, "Chromium"


# ---------------------------------------------------------------------------
# Distribuição de portas CDP por conta
# ---------------------------------------------------------------------------

def _port_for_account(account_id: str) -> int:
    """
    Mapeia account_id → porta CDP estável e única.

    Usar a mesma porta para o mesmo account_id permite que execuções
    consecutivas reconectem ao mesmo perfil. Usar portas diferentes para
    contas diferentes permite bootstrap em paralelo.
    """
    # Hash determinístico → offset 0..999. Base 9222 → range 9222..10221.
    h = abs(hash(account_id)) % 1000
    return CDP_PORT_BASE + h


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_for_port(port: int, timeout_s: float) -> bool:
    """Aguarda até `timeout_s` segundos pela porta TCP aceitar conexões."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# Argumentos de launch
# ---------------------------------------------------------------------------

def _launch_args_for_real_browser(port: int, profile_path: str) -> list[str]:
    """
    Flags mínimas para o navegador real. Evitamos qualquer flag que seja
    sinal clássico de automação (--no-sandbox, --disable-gpu, etc).

    Notas:
      * --remote-debugging-port abre o CDP.
      * --user-data-dir isola o perfil do usuário "real" do navegador.
      * --no-first-run / --no-default-browser-check pulam dialogs iniciais.
      * --restore-last-session=false NÃO é passado: deixar o navegador agir
        como agiria normalmente.
    """
    return [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        # NÃO incluir:
        #   --disable-blink-features=AutomationControlled  → o nosso script de
        #     stealth já cuida de webdriver=undefined e o "AutomationControlled"
        #     feature flag pode ser detectado em si.
        #   --no-sandbox / --disable-dev-shm-usage / --disable-extensions
        #     → todos são padrões de container/CI, fáceis de detectar.
    ]


def _fallback_persistent_args() -> list[str]:
    """Args para o fallback persistent context do Chromium bundled."""
    return [
        "--no-first-run",
        "--no-default-browser-check",
        # Sem --no-sandbox de propósito (ver acima).
    ]


# ---------------------------------------------------------------------------
# Função pública
# ---------------------------------------------------------------------------

def launch_browser(
    account_id: str,
    *,
    headless: bool = False,
    log: Optional[callable] = None,
) -> LaunchResult:
    """
    Abre um navegador para a conta `account_id` usando a melhor estratégia
    disponível no host.

    Parâmetros:
      account_id : ID estável da conta (usado para perfil e porta CDP).
      headless   : Se True, força modo headless (apenas no fallback persistent
                   — CDP com Edge/Chrome real ignora isso porque queremos que o
                   usuário possa resolver CAPTCHA manualmente).
      log        : Callable opcional para mensagens de progresso.

    Retorna: LaunchResult.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    os.makedirs(PROFILES_DIR, exist_ok=True)
    profile_path = os.path.join(PROFILES_DIR, account_id)
    os.makedirs(profile_path, exist_ok=True)

    # ── Etapa 1: tenta navegador real via CDP ───────────────────────────
    binary, name = _find_browser_binary()
    if binary is not None:
        port = _port_for_account(account_id)

        # Se a porta já está em uso, tentamos uma sequência adjacente.
        original_port = port
        for offset in range(0, 50):
            candidate = original_port + offset
            if _port_is_free(candidate):
                port = candidate
                break
        else:
            _log(f"Nenhuma porta livre no range {original_port}..{original_port+49}; "
                 "caindo para persistent context.")
            binary = None  # força fallback

    if binary is not None:
        _log(f"Lançando {name} via CDP (porta {port}, perfil em {profile_path})…")
        try:
            args = [binary] + _launch_args_for_real_browser(port, profile_path)
            # CREATE_NO_WINDOW para não roubar foco; o navegador real abre
            # sua própria janela visível.
            creation_flags = 0
            if sys.platform == "win32":
                # DETACHED_PROCESS: o navegador continua rodando mesmo se o
                # processo Python morrer (queremos controle explícito de fim).
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

            if not _wait_for_port(port, CDP_CONNECT_TIMEOUT_S):
                _log(f"⚠️  Porta CDP {port} não respondeu — caindo para persistent.")
                try:
                    proc.terminate()
                except Exception:
                    pass
                binary = None  # força fallback abaixo
            else:
                # Conecta o Playwright via CDP.
                pw = sync_playwright().start()
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")

                # connect_over_cdp retorna um Browser com um context default
                # já contendo todas as páginas existentes. Pegamos esse context.
                contexts = browser.contexts
                context = contexts[0] if contexts else browser.new_context()

                # Aplica o script de stealth — barato e sem efeitos colaterais.
                from browser.stealth import apply_stealth
                apply_stealth(context)

                _log(f"✅ Conectado ao {name} via CDP.")
                return LaunchResult(
                    pw=pw,
                    context=context,
                    subprocess=proc,
                    mode="cdp",
                    browser_name=name,
                )
        except Exception as exc:
            _log(f"⚠️  Falha ao iniciar CDP ({exc}); caindo para persistent.")
            try:
                if 'proc' in locals():
                    proc.terminate()
            except Exception:
                pass
            binary = None  # força fallback

    # ── Etapa 2: fallback persistent context ────────────────────────────
    _log("Usando Playwright launch_persistent_context (Chromium bundled).")
    pw = sync_playwright().start()

    # Tenta channel="msedge" → "chrome" → bundled.
    last_exc: Optional[Exception] = None
    for channel in ("msedge", "chrome", None):
        try:
            launch_kwargs: dict = dict(
                user_data_dir=profile_path,
                headless=headless,
                args=_fallback_persistent_args(),
                no_viewport=True,  # tamanho de janela nativo, sem emulação
                locale="en-GB",
                timezone_id="Europe/London",
                extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
            )
            if channel:
                launch_kwargs["channel"] = channel
            else:
                # Só sobrescreve UA no Chromium bundled.
                launch_kwargs["user_agent"] = USER_AGENT

            context = pw.chromium.launch_persistent_context(**launch_kwargs)
            from browser.stealth import apply_stealth
            apply_stealth(context)

            label = channel or "chromium-bundled"
            _log(f"✅ Persistent context iniciado (channel={label}).")
            return LaunchResult(
                pw=pw,
                context=context,
                subprocess=None,
                mode="persistent",
                browser_name=channel.title() if channel else "Chromium",
            )
        except Exception as exc:
            last_exc = exc
            continue

    # Todos os channels falharam — propaga
    try:
        pw.stop()
    except Exception:
        pass
    raise RuntimeError(f"Não foi possível iniciar nenhum navegador: {last_exc!r}")
