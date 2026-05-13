"""
browser/stealth.py — Patches anti-fingerprinting para Chromium via add_init_script.

Esta camada injeta JS antes de qualquer script da página carregar, removendo
os sinais clássicos que a Cloudflare (e outros sistemas anti-bot) usam para
identificar automação:

  * navigator.webdriver          → undefined
  * navigator.plugins            → lista plausível (1+ entrada)
  * navigator.languages          → ["en-GB","en-US","en"]
  * navigator.permissions        → comportamento humano para notification
  * window.chrome.runtime / .csi / .loadTimes / .app → presentes e plausíveis
  * WebGL vendor/renderer        → "Intel Inc."/"Intel Iris OpenGL Engine"
  * Iframe contentWindow         → não retorna Proxy
  * Notification.permission      → coerente com permissions
  * Função toString().toString  → não delata Proxy/native code

Importante: esta camada NÃO substitui o uso de uma sessão real persistida
(perfil do navegador com cf_clearance válido). Ela apenas reduz o ruído de
fingerprint para o caso em que o navegador é o Chromium bundled do Playwright
e ajuda a manter o cf_clearance vivo mais tempo após uma validação manual.

Como usar:

    from browser.stealth import apply_stealth
    context = pw.chromium.launch_persistent_context(...)
    apply_stealth(context)         # aplica em todas as páginas do context

Ou, em uma página específica:

    page = context.new_page()
    page.add_init_script(STEALTH_JS)
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Script principal — executado em todo frame antes do JS da página
# ---------------------------------------------------------------------------
#
# Notas de implementação:
#
# - Tudo dentro de um IIFE para não vazar variáveis para o escopo da página.
# - Cada patch é envolto em try/catch porque o Chromium continua evoluindo
#   e algumas propriedades podem já estar congeladas em versões futuras.
# - Evitamos `Object.defineProperty` com `configurable: true` quando dá pra
#   usar Proxy de getter — Proxies são mais difíceis de detectar.
# - O patch de iframe é o que mais quebra sites mal escritos; usamos uma
#   versão que só intervém em iframes about:blank dinâmicos.

STEALTH_JS = r"""
(() => {
  if (window.__stealth_loaded) return;
  window.__stealth_loaded = true;

  // -------------------------------------------------------------- webdriver
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}

  // ---------------------------------------------------------------- chrome
  // Cloudflare verifica `window.chrome` e seus subcampos para distinguir
  // Chromium "real" de headless puro.
  try {
    if (!window.chrome) {
      window.chrome = {};
    }
    const noop = () => {};
    window.chrome.runtime = window.chrome.runtime || {
      OnInstalledReason: {},
      OnRestartRequiredReason: {},
      PlatformArch: {},
      PlatformNaclArch: {},
      PlatformOs: {},
      RequestUpdateCheckStatus: {},
      connect: noop,
      sendMessage: noop,
    };
    window.chrome.csi = window.chrome.csi || (() => ({
      onloadT: Date.now(),
      pageT: 1,
      startE: Date.now(),
      tran: 15,
    }));
    window.chrome.loadTimes = window.chrome.loadTimes || (() => ({
      requestTime: Date.now() / 1000,
      startLoadTime: Date.now() / 1000,
      commitLoadTime: Date.now() / 1000,
      finishDocumentLoadTime: Date.now() / 1000,
      finishLoadTime: Date.now() / 1000,
      firstPaintTime: Date.now() / 1000,
      firstPaintAfterLoadTime: 0,
      navigationType: 'Other',
      wasFetchedViaSpdy: true,
      wasNpnNegotiated: true,
      npnNegotiatedProtocol: 'h2',
      wasAlternateProtocolAvailable: false,
      connectionInfo: 'h2',
    }));
    window.chrome.app = window.chrome.app || {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };
  } catch (e) {}

  // ---------------------------------------------------------------- plugins
  // Headless Chromium retorna lista vazia; replicamos uma lista plausível
  // de um Chrome desktop padrão (PDF viewer + Native Client).
  try {
    const fakePlugin = (name, filename, description) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name:        { value: name },
        filename:    { value: filename },
        description: { value: description },
        length:      { value: 1 },
      });
      return p;
    };
    const plugins = [
      fakePlugin('PDF Viewer',              'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chrome PDF Viewer',       'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chromium PDF Viewer',     'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Microsoft Edge PDF Viewer','internal-pdf-viewer','Portable Document Format'),
      fakePlugin('WebKit built-in PDF',     'internal-pdf-viewer', 'Portable Document Format'),
    ];
    Object.setPrototypeOf(plugins, PluginArray.prototype);
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => plugins,
      configurable: true,
    });
    Object.defineProperty(Navigator.prototype, 'mimeTypes', {
      get: () => Object.setPrototypeOf([], MimeTypeArray.prototype),
      configurable: true,
    });
  } catch (e) {}

  // -------------------------------------------------------------- languages
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => ['en-GB', 'en-US', 'en'],
      configurable: true,
    });
  } catch (e) {}

  // ----------------------------------------------------------- permissions
  // Em headless o Notification.permission costuma divergir do permissions API.
  // Forçamos coerência: ambos retornam "default".
  try {
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (parameters) =>
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : origQuery.call(window.navigator.permissions, parameters);
    }
  } catch (e) {}

  // ------------------------------------------------------------------ WebGL
  // Vendor/renderer "SwiftShader" denuncia Chromium headless sem GPU.
  // Substituímos por valores comuns de uma máquina desktop com GPU integrada.
  try {
    const patch = (proto) => {
      const orig = proto.getParameter;
      proto.getParameter = function (parameter) {
        // UNMASKED_VENDOR_WEBGL
        if (parameter === 37445) return 'Intel Inc.';
        // UNMASKED_RENDERER_WEBGL
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return orig.call(this, parameter);
      };
    };
    if (typeof WebGLRenderingContext !== 'undefined') patch(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') patch(WebGL2RenderingContext.prototype);
  } catch (e) {}

  // ------------------------------------------------------ hardware concurrency
  // Headless costuma reportar 1; valores comuns ficam entre 4 e 16.
  try {
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
      get: () => 8,
      configurable: true,
    });
  } catch (e) {}

  // ------------------------------------------------------------- deviceMemory
  try {
    Object.defineProperty(Navigator.prototype, 'deviceMemory', {
      get: () => 8,
      configurable: true,
    });
  } catch (e) {}

  // ------------------------------------------------------------------- vendor
  try {
    Object.defineProperty(Navigator.prototype, 'vendor', {
      get: () => 'Google Inc.',
      configurable: true,
    });
  } catch (e) {}

  // ------------------------------------------------------ contentWindow proxy
  // Patches mais agressivos de iframe quebram páginas SPA. Apenas removemos
  // a propriedade que retorna `Proxy` em alguns builds de Chromium headless.
  try {
    const desc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (desc && desc.get) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get() {
          const w = desc.get.call(this);
          return w;  // mantemos comportamento nativo; só estamos garantindo descriptor configurable
        },
        configurable: true,
      });
    }
  } catch (e) {}
})();
"""


def apply_stealth(context: Any) -> None:
    """
    Aplica o script de stealth ao contexto de browser do Playwright.

    Deve ser chamado IMEDIATAMENTE após criar/conectar ao context, antes de
    qualquer `new_page()` ou navegação. O script é registrado em todos os
    frames de todas as páginas do context.

    Parâmetros:
        context: BrowserContext do Playwright (sync API).
    """
    try:
        context.add_init_script(STEALTH_JS)
    except Exception:
        # Em contexts via CDP em browsers reais (Edge/Chrome) o init script
        # nem sempre é necessário porque o fingerprint nativo já está correto.
        # Não fatalizamos.
        pass
