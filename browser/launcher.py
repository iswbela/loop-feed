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
# Controle de processos
# ---------------------------------------------------------------------------

_BROWSER_EXE_NAMES = {
    "chrome": "chrome.exe",
    "opera":  "opera.exe",
}


def kill_browser(browser_name: str, status_cb: Optional[Callable[[str], None]] = None) -> None:
    """
    Encerra todas as instâncias do browser informado via taskkill.
    Necessário para que o --remote-debugging-port seja aceito na próxima inicialização
    (o Chrome ignora a flag se já existir outro processo rodando).
    """
    exe = _BROWSER_EXE_NAMES.get(browser_name.lower(), "chrome.exe")
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", exe, "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode == 0:
            if status_cb:
                status_cb(f"🛑 Instâncias de {exe} encerradas.")
            time.sleep(1.5)  # aguarda o processo morrer de fato
        else:
            # Código 128 = processo não encontrado (já fechado)
            if status_cb:
                status_cb(f"ℹ️ Nenhuma instância de {exe} em execução.")
    except Exception as e:
        if status_cb:
            status_cb(f"⚠️ Não foi possível encerrar {exe}: {e}")


def launch_with_cdp(
    exe_path: str,
    profile_dir: Optional[Path] = None,
    extra_args: Optional[list] = None,
) -> subprocess.Popen:
    """
    Inicia o browser com remote debugging habilitado na porta CDP_PORT.
    Usa APP_PROFILE_DIR como perfil persistente por padrão.

    Args:
        exe_path:    Caminho para o executável do browser.
        profile_dir: Diretório do perfil (padrão: APP_PROFILE_DIR).
        extra_args:  Argumentos adicionais opcionais.

    Returns:
        O objeto Popen do processo iniciado.
    """
    if profile_dir is None:
        profile_dir = APP_PROFILE_DIR

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = [
        str(exe_path),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Oculta indicadores de automação do DevTools
        "--disable-blink-features=AutomationControlled",
        # Desabilita extensões que podem interferir
        "--disable-extensions-except=",
        # Janela normal (não minimizada)
        "--start-maximized",
    ]

    if extra_args:
        args.extend(extra_args)

    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Desanexa do processo pai para que o browser não feche junto com o app
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt" else 0,
    )


# ---------------------------------------------------------------------------
# Ponto de entrada principal
# ---------------------------------------------------------------------------

def ensure_browser_with_cdp(
    status_cb: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """
    Garante que um browser com CDP esteja acessível em CDP_ENDPOINT.

    Fluxo:
      1. Verifica se o CDP já está disponível  → reutiliza (zero impacto)
      2. Detecta o melhor browser instalado
      3. Encerra instâncias existentes do browser (necessário para Chrome aceitar CDP)
      4. Inicia o browser com --remote-debugging-port
      5. Aguarda o CDP ficar disponível
      6. Valida e retorna

    Returns:
        (True, mensagem_ok)  se o CDP ficou disponível.
        (False, mensagem_erro) caso contrário.
    """
    def _s(msg: str) -> None:
        print(f"[BROWSER] {msg}", flush=True)
        if status_cb:
            status_cb(msg)

    # ── 1. CDP já disponível? ────────────────────────────────────────────────
    ok, info = cdp_info()
    if ok:
        ver = info.get("Browser", "desconhecido") if info else "desconhecido"
        _s(f"✅ CDP já disponível → {ver}")
        return True, f"CDP disponível: {ver}"

    # ── 2. Detectar browser ──────────────────────────────────────────────────
    browser_name, exe_path = detect_browser()
    if not exe_path:
        msg = (
            "❌ Nenhum browser compatível encontrado.\n"
            "Instale o Google Chrome, Opera ou Opera GX e tente novamente."
        )
        _s(msg)
        return False, msg

    _s(f"🔍 Browser detectado: {browser_name} → {exe_path}")
    _s(f"📁 Perfil persistente: {APP_PROFILE_DIR}")

    # ── 3. Encerrar instâncias existentes ────────────────────────────────────
    # O Chrome ignora --remote-debugging-port se já existe um processo em execução.
    # É necessário fechar todas as instâncias antes de relançar.
    _s(f"🛑 Encerrando instâncias existentes de {browser_name}…")
    kill_browser(browser_name, status_cb=_s)

    # ── 4. Iniciar com CDP ───────────────────────────────────────────────────
    _s(f"🚀 Iniciando {browser_name} com CDP na porta {CDP_PORT}…")
    try:
        launch_with_cdp(exe_path)
    except Exception as e:
        msg = f"❌ Falha ao iniciar o browser: {e}"
        _s(msg)
        return False, msg

    # ── 5. Aguardar CDP ──────────────────────────────────────────────────────
    _s("⏳ Aguardando CDP ficar disponível…")
    if not wait_for_cdp(timeout=_CDP_WAIT_TIMEOUT):
        msg = (
            f"❌ Timeout: CDP não ficou disponível após {_CDP_WAIT_TIMEOUT:.0f}s.\n"
            "Verifique se o browser iniciou corretamente."
        )
        _s(msg)
        return False, msg

    # ── 6. Validar ───────────────────────────────────────────────────────────
    ok, info = cdp_info()
    ver = info.get("Browser", "desconhecido") if info else "desconhecido"
    _s(f"✅ Browser pronto: {ver}")
    return True, f"CDP disponível: {ver}"
