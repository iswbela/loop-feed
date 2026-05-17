"""
browser/launcher.py — Gerenciador de browser via CDP (Chrome DevTools Protocol)

Responsável por:
  - Detectar automaticamente Chrome, Opera e Opera GX instalados
  - Encerrar instâncias existentes do navegador escolhido
  - Iniciar o browser com --remote-debugging-port
  - Aguardar o CDP ficar disponível
  - Expor ensure_browser_with_cdp() como ponto de entrada principal

Filosofia: NUNCA usa playwright.launch() nem launchPersistentContext().
Sempre conecta via connectOverCDP() para evitar detecção de automação.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional, Tuple

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CDP_PORT: int = 9222
CDP_ENDPOINT: str = f"http://127.0.0.1:{CDP_PORT}"

# Diretório de perfil persistente exclusivo da aplicação.
# Fica em %APPDATA%\LoopFeed\BrowserProfile — persiste entre execuções,
# mantendo cookies, sessão e histórico da automação.
APP_PROFILE_DIR: Path = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    / "LoopFeed"
    / "BrowserProfile"
)

# Timeout (segundos) para o CDP ficar disponível após o launch
_CDP_WAIT_TIMEOUT: float = 25.0
_CDP_POLL_INTERVAL: float = 0.5

# ---------------------------------------------------------------------------
# Detecção de navegadores
# ---------------------------------------------------------------------------

def _env(var: str) -> Path:
    """Retorna Path para uma variável de ambiente, ou Path vazio se ausente."""
    return Path(os.environ.get(var, ""))


def _find_chrome() -> Optional[str]:
    """Localiza o executável do Google Chrome."""
    candidates = [
        _env("LOCALAPPDATA") / "Google" / "Chrome" / "Application" / "chrome.exe",
        _env("ProgramFiles")  / "Google" / "Chrome" / "Application" / "chrome.exe",
        _env("ProgramFiles(x86)") / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _find_opera() -> Optional[str]:
    """Localiza o executável do Opera ou Opera GX (suporta subpastas versionadas)."""
    base_dirs = [
        _env("LOCALAPPDATA") / "Programs" / "Opera",
        _env("LOCALAPPDATA") / "Programs" / "Opera GX",
        _env("ProgramFiles")  / "Opera",
        _env("ProgramFiles(x86)") / "Opera",
    ]
    for base in base_dirs:
        if not base.exists():
            continue
        # Caminho direto
        direct = base / "opera.exe"
        if direct.exists():
            return str(direct)
        # Subpastas versionadas (ex: 115.0.5322.77/opera.exe)
        matches = sorted(glob.glob(str(base / "*" / "opera.exe")))
        if matches:
            return matches[-1]  # versão mais recente (último por ordem alfabética)
    return None


# Ordem de preferência: Chrome primeiro, depois Opera / Opera GX
_BROWSER_DETECTORS = [
    ("chrome", _find_chrome),
    ("opera",  _find_opera),
]


def detect_browser() -> Tuple[Optional[str], Optional[str]]:
    """
    Detecta o melhor browser disponível.
    Retorna (browser_name, exe_path) ou (None, None) se nenhum encontrado.
    Chrome tem prioridade sobre Opera/Opera GX.
    """
    for name, finder in _BROWSER_DETECTORS:
        exe = finder()
        if exe:
            return name, exe
    return None, None


# ---------------------------------------------------------------------------
# CDP — verificação de disponibilidade
# ---------------------------------------------------------------------------

def cdp_info() -> Tuple[bool, Optional[dict]]:
    """
    Consulta http://127.0.0.1:{CDP_PORT}/json/version.
    Retorna (True, info_dict) se disponível, (False, None) caso contrário.
    """
    try:
        with urllib.request.urlopen(f"{CDP_ENDPOINT}/json/version", timeout=2) as r:
            data = json.loads(r.read().decode())
            return True, data
    except Exception:
        return False, None


def wait_for_cdp(timeout: float = _CDP_WAIT_TIMEOUT) -> bool:
    """
    Aguarda o CDP ficar disponível com polling.
    Retorna True se ficou disponível dentro do timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, _ = cdp_info()
        if ok:
            return True
        time.sleep(_CDP_POLL_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Launch e ensure
# ---------------------------------------------------------------------------

def launch_with_cdp(exe_path: str, status_cb: Optional[Callable] = None) -> bool:
    """
    Inicia o browser com --remote-debugging-port e --user-data-dir persistente.
    Retorna True se o CDP ficou disponível dentro do timeout.
    """
    def s(msg):
        print(msg, flush=True)
        if status_cb:
            status_cb(msg)

    APP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    args = [
        exe_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={APP_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]

    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        s(f"Browser iniciado. Aguardando CDP na porta {CDP_PORT}...")
        return wait_for_cdp()
    except Exception as exc:
        s(f"Erro ao iniciar browser: {exc}")
        return False


def ensure_browser_with_cdp(status_cb: Optional[Callable] = None) -> Tuple[bool, str]:
    """
    Garante que um browser compatível está rodando com CDP ativo.

    1. Se CDP já estiver disponível → retorna (True, "CDP já disponível").
    2. Caso contrário, detecta e inicia o browser, aguarda CDP.
    3. Se nenhum browser for encontrado → retorna (False, mensagem de erro).

    Retorna (success: bool, message: str).
    """
    def s(msg):
        print(msg, flush=True)
        if status_cb:
            status_cb(msg)

    # Verifica se CDP já está ativo (browser já aberto)
    ok, info = cdp_info()
    if ok:
        browser_name = (info or {}).get("Browser", "Browser")
        s(f"CDP disponivel: {browser_name}")
        return True, f"CDP disponivel: {browser_name}"

    # Detecta browser disponível
    s("CDP nao disponivel. Detectando browser instalado...")
    name, exe = detect_browser()
    if not exe:
        msg = (
            "Nenhum browser compativel encontrado (Chrome, Opera ou Opera GX). "
            "Instale um desses navegadores e tente novamente."
        )
        s(msg)
        return False, msg

    s(f"Browser detectado: {name} ({exe})")
    launched = launch_with_cdp(exe, status_cb=status_cb)
    if launched:
        return True, f"{name} iniciado com CDP na porta {CDP_PORT}."
    else:
        msg = f"Browser iniciado mas CDP nao ficou disponivel em {_CDP_WAIT_TIMEOUT}s."
        s(msg)
        return False, msg
