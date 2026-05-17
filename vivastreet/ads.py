"""
vivastreet/ads.py — Anúncios VivaStreet via CDP browser.

Fluxo após autenticação bem-sucedida:
  1. load_vs_ads(): abre home /user/account/ads, extrai lista de anúncios
     via API + imagens da thumbnail da página. Não edita nada.
  2. toggle_heart_description(): acionado pelo usuário ao clicar em Boost.
     Percorre as 5 páginas do wizard de edição com delays humanos.
"""

import json as _json
import random
import time
import threading
import traceback
from typing import Callable, Optional

from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError as _CurlError

from config import VIVASTREET_BASE_URL, USER_AGENT
from utils import ts
from vivastreet.tokens import load_tokens, load_cookies

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_API_CLASSIFIEDS = f"{VIVASTREET_BASE_URL}/api/v2/classified"
_EDIT_URL_TPL    = f"{VIVASTREET_BASE_URL}/post?modify={{ad_id}}"
_REQUEST_TIMEOUT = 20   # segundos
_HEART           = "❤️"

_BASE_HEADERS = {
    "User-Agent":      USER_AGENT,
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer":         f"{VIVASTREET_BASE_URL}/user/account/ads",
    "Origin":          VIVASTREET_BASE_URL,
}

_API_HEADERS = {
    "User-Agent":      USER_AGENT,
    "Accept":          "application/json",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer":         f"{VIVASTREET_BASE_URL}/user/account/ads",
    "Origin":          VIVASTREET_BASE_URL,
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def load_vs_ads(
    account_id: str,
    status_cb: Optional[Callable[[str], None]] = None,
    done_cb:   Optional[Callable[[bool, str, list], None]] = None,
) -> None:
    """
    Carrega a lista de anúncios do account_id a partir da home /user/account/ads.
    Extrai imagens direto do DOM da página. Não edita nem navega para páginas
    de edição. Roda em thread daemon e retorna imediatamente.

    done_cb(success, message, ads)
      ads — lista de dicts: {ad_id, title, edit_url, image_url}
    """
    threading.Thread(
        target=_run_load_ads,
        args=(account_id, status_cb, done_cb),
        daemon=True,
    ).start()


def toggle_heart_description(
    account_id: str,
    ad_id: str,
    edit_url: str,
    status_cb: Optional[Callable[[str], None]] = None,
    done_cb:   Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Abre a tela de edição do anúncio via CDP, lê a descrição atual e
    adiciona ❤️ ao final se não houver, ou remove se houver.
    Roda em thread daemon e retorna imediatamente.

    done_cb(success, message)
    """
    threading.Thread(
        target=_run_toggle_heart,
        args=(account_id, ad_id, edit_url, status_cb, done_cb),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Implementação interna
# ---------------------------------------------------------------------------

def _log(account_id: str, msg: str) -> None:
    print(f"{ts()} [VS:ADS:{account_id[:8]}] {msg}", flush=True)


def _build_session(account_id: str, access_token: str) -> requests.Session:
    """
    Monta uma curl_cffi Session com:
      - Todos os cookies salvos pelo Playwright injetados como Cookie header string
        (evita problemas de domain matching que causam 403 do Cloudflare)
      - Header Authorization: Bearer {access_token}
      - Impersonação Chrome124 para passar pelo fingerprint TLS do Cloudflare
    """
    session = requests.Session(impersonate="chrome124")

    # Carrega cookies e injeta como string no header Cookie
    # Isso garante que cf_clearance e demais cookies sejam enviados independente de domínio
    raw_cookies = load_cookies(account_id)
    cookie_parts = [
        f"{ck['name']}={ck['value']}"
        for ck in raw_cookies
        if ck.get("name") and ck.get("value")
    ]
    injected = len(cookie_parts)
    cookie_header = "; ".join(cookie_parts)

    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept-Language": "en-GB,en;q=0.9",
        "Authorization":   f"Bearer {access_token}",
        "Cookie":          cookie_header,
    })
    return session, injected


def _run_toggle_heart(account_id, ad_id, edit_url, status_cb, done_cb):
    """
    Fluxo completo de boost VivaStreet (5 páginas):
      Pág 1 — Edição da descrição (toggle ❤️)  → Next
      Pág 2 — Fotos                             → Next
      Pág 3 — Confirmação                       → Confirm your ad
      Pág 4 — Validação automática              → aguarda redirect do site
      Pág 5 — Premium Options                  → fluxo concluído

    Delays humanos aleatórios de 3–4 s entre cada página.
    """

    def s(msg: str) -> None:
        _log(account_id, msg)
        if status_cb:
            status_cb(msg)

    def _human_delay():
        ms = random.randint(3_000, 4_000)
        s(f"Aguardando {ms // 1000}s antes de continuar…")
        page.wait_for_timeout(ms)

    pw = cdp_browser = page = None
    page_was_created = False

    try:
        pw, cdp_browser, _, page, page_was_created = _open_cdp_session(s)

        # ── Página 1: Descrição ──────────────────────────────────────────
        s(f"[1/5] Abrindo tela de edição do anúncio {ad_id}…")
        resp = page.goto(edit_url, wait_until="domcontentloaded", timeout=30_000)

        if resp and resp.status not in (200,):
            msg = f"❌ HTTP {resp.status} ao abrir tela de edição."
            s(msg)
            if done_cb:
                done_cb(False, msg)
            return

        try:
            page.wait_for_selector('textarea[name="detail"]', timeout=15_000)
        except Exception:
            msg = "❌ Campo de descrição não encontrado na página 1."
            s(msg)
            if done_cb:
                done_cb(False, msg)
            return

        current_desc = page.input_value('textarea[name="detail"]')
        s(f"[1/5] Descrição atual ({len(current_desc)} chars): "
          f"{current_desc[:60]}{'…' if len(current_desc) > 60 else ''}")

        stripped = current_desc.rstrip()
        if stripped.endswith(_HEART):
            new_desc = stripped[: -len(_HEART)].rstrip()
            action   = "❤️ removido"
        else:
            new_desc = stripped + " " + _HEART
            action   = "❤️ adicionado"

        page.fill('textarea[name="detail"]', new_desc)
        page.wait_for_timeout(500)
        s(f"[1/5] {action} — clicando em Next…")
        page.click('button[type="submit"]')

        # ── Página 2: Fotos ──────────────────────────────────────────────
        _human_delay()
        s("[2/5] Aguardando página de fotos…")
        try:
            page.wait_for_selector('#step2Submit', state="visible", timeout=20_000)
        except Exception:
            msg = "❌ Botão Next (fotos) não encontrado na página 2."
            s(msg)
            if done_cb:
                done_cb(False, msg)
            return

        s("[2/5] Clicando em Next (fotos)…")
        page.click('#step2Submit')

        # ── Página 3: Confirmação ────────────────────────────────────────
        _human_delay()
        s("[3/5] Aguardando página de confirmação…")
        try:
            page.wait_for_selector('#publish_button', state="visible", timeout=20_000)
        except Exception:
            msg = "❌ Botão 'Confirm your ad' não encontrado na página 3."
            s(msg)
            if done_cb:
                done_cb(False, msg)
            return

        # Aceita os Termos e Condições se o checkbox ainda não estiver marcado
        try:
            term_cb = page.locator('#term_checkbox')
            if term_cb.count() > 0 and not term_cb.is_checked():
                s("[3/5] Aceitando Termos e Condições…")
                term_cb.check()
                page.wait_for_timeout(300)
        except Exception as tc_exc:
            s(f"[3/5] Aviso ao marcar termos: {tc_exc.__class__.__name__} — continuando.")

        s("[3/5] Clicando em 'Confirm your ad'…")
        page.click('#publish_button')

        # ── Página 4: Validação — aguarda redirect automático ────────────
        _human_delay()
        s("[4/5] Aguardando tela de validação e redirecionamento automático…")

        try:
            # Espera a página carregar após o submit
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
            url_validation = page.url

            # Detecta se estamos na tela de validação ("Please wait…")
            is_validation = page.evaluate("""() => {
                return document.body.innerText.toLowerCase().includes('please wait') ||
                       !!document.querySelector('.processing-svg, [class*="processing"]');
            }""")

            if is_validation:
                s(f"[4/5] Tela de validação detectada (URL: {url_validation}). "
                  "Aguardando redirecionamento automático (até 2 min)…")
                # Aguarda até que a URL mude — o site redireciona sozinho
                deadline = time.time() + 120
                while time.time() < deadline:
                    page.wait_for_timeout(2_000)
                    if page.url != url_validation:
                        s(f"[4/5] Redirecionado para: {page.url}")
                        break
                else:
                    s("[4/5] ⚠️ Timeout aguardando redirecionamento da validação.")
            else:
                s(f"[4/5] Validação não detectada — continuando (URL: {page.url}).")

        except Exception as exc:
            s(f"[4/5] Aviso ao aguardar validação: {exc}")

        # ── Página 5: Premium Options ────────────────────────────────────
        s(f"[5/5] Fluxo concluído. URL final: {page.url}")
        msg = f"✅ {action} com sucesso! Anúncio publicado (ID {ad_id})."
        s(msg)
        if done_cb:
            done_cb(True, msg)

    except Exception as exc:
        traceback.print_exc()
        msg = f"❌ Erro no fluxo de edição: {exc}"
        s(msg)
        if done_cb:
            done_cb(False, msg)

    finally:
        _close_cdp_session(pw, cdp_browser, page, page_was_created)


def _open_cdp_session(s):
    """
    Garante que o Chrome está rodando com CDP e conecta.
    Retorna (pw, cdp_browser, context, page, page_was_created).
    Levanta RuntimeError se CDP não estiver disponível.
    """
    from playwright.sync_api import sync_playwright
    from browser.launcher import CDP_ENDPOINT, ensure_browser_with_cdp

    ok, msg = ensure_browser_with_cdp(status_cb=s)
    if not ok:
        raise RuntimeError(f"CDP indisponível: {msg}")

    pw          = sync_playwright().start()
    cdp_browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=5000)
    contexts    = cdp_browser.contexts
    context     = contexts[0] if contexts else cdp_browser.new_context()

    # Reutiliza aba já em vivastreet; caso contrário abre uma nova
    existing = next((p for p in context.pages if "vivastreet" in p.url), None)
    if existing:
        page             = existing
        page_was_created = False
    else:
        page = context.new_page()
        page.goto(
            f"{VIVASTREET_BASE_URL}/user/account/ads",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        page_was_created = True

    return pw, cdp_browser, context, page, page_was_created


def _close_cdp_session(pw, cdp_browser, page, page_was_created):
    """Fecha a aba criada (se foi criada aqui) e desconecta do CDP sem fechar o Chrome."""
    try:
        if page_was_created and page:
            page.close()
    except Exception:
        pass
    try:
        if cdp_browser:
            cdp_browser.close()
    except Exception:
        pass
    try:
        if pw:
            pw.stop()
    except Exception:
        pass


def _fetch_ad_list(page, account_id: str, access_token: str, s) -> list[dict]:
    """
    Chama /api/v2/classified de dentro do Chrome real (página CDP já aberta).
    Usa fetch() com credentials:include para que os cookies (cf_clearance) sejam
    enviados automaticamente pelo browser — evita o bloqueio do Cloudflare.
    Retorna lista de dicts: {id, title}.
    """
    s("Buscando lista de anúncios via browser (CDP)…")

    token_js = _json.dumps(access_token)
    api_url  = _json.dumps(f"{_API_CLASSIFIEDS}?page=1&per_page=50")
    base_url = _json.dumps(f"{VIVASTREET_BASE_URL}/user/account/ads")

    try:
        js_result = page.evaluate(f"""async () => {{
            try {{
                const resp = await fetch({api_url}, {{
                    method: 'GET',
                    credentials: 'include',
                    headers: {{
                        'Authorization': 'Bearer ' + {token_js},
                        'Accept': 'application/json',
                        'Accept-Language': 'en-GB,en;q=0.9',
                        'Referer': {base_url}
                    }}
                }});
                const text = await resp.text();
                return {{ ok: true, status: resp.status, body: text }};
            }} catch(e) {{
                return {{ ok: false, error: String(e) }};
            }}
        }}""")
    except Exception as exc:
        s(f"❌ Erro ao executar fetch no browser: {exc}")
        return []

    if not js_result.get("ok"):
        s(f"❌ Erro no fetch via browser: {js_result.get('error')}")
        return []

    status = js_result.get("status")
    _log(account_id, f"fetch /api/v2/classified via browser → HTTP {status}")

    if status == 200:
        data        = _json.loads(js_result["body"])
        classifieds = data.get("data", [])
        s(f"✅ API retornou {len(classifieds)} anúncio(s).")
        return [
            {"id": str(c.get("id", "")), "title": c.get("title", f"Anúncio {c.get('id', '?')}")}
            for c in classifieds if c.get("id")
        ]
    elif status == 401:
        s("❌ HTTP 401 — token expirado. Faça login novamente.")
    elif status == 403:
        s(f"❌ HTTP 403 — Cloudflare bloqueou mesmo via browser. "
          f"Body: {js_result.get('body', '')[:300]}")
    else:
        s(f"HTTP {status}: {js_result.get('body', '')[:200]}")
    return []



def _extract_images_from_page(page) -> dict:
    """Extrai a primeira thumbnail de cada anuncio do DOM. Retorna {ad_id: url}."""
    try:
        return page.evaluate(
            """() => {
                const imgs = {};
                document.querySelectorAll('img').forEach(function(img) {
                    if (!img.src) return;
                    var m = img.src.match(
                        /viva-images\\.com\\/[^\\/]+\\/clad\\/(\\d+)\\/[^.]+\\.(jpeg|jpg|png)/i
                    );
                    if (m && !imgs[m[1]]) imgs[m[1]] = img.src;
                });
                return imgs;
            }"""
        )
    except Exception:
        return {}


def _run_load_ads(account_id, status_cb, done_cb):
    """
    Navega para /user/account/ads, busca anuncios via API e extrai thumbnails.
    Nao edita nada.
    """

    def s(msg):
        _log(account_id, msg)
        if status_cb:
            status_cb(msg)

    ads = []
    pw = cdp_browser = page = None
    page_was_created = False

    try:
        tokens = load_tokens(account_id)
        access_token = tokens.get("access_token", "")

        if not access_token:
            msg = "Nenhum access_token. Faca login antes."
            s(msg)
            if done_cb:
                done_cb(False, msg, [])
            return

        pw, cdp_browser, _, page, page_was_created = _open_cdp_session(s)

        s("Carregando home de anuncios...")
        page.goto(
            VIVASTREET_BASE_URL + "/user/account/ads",
            wait_until="domcontentloaded",
            timeout=30000,
        )

        try:
            page.wait_for_selector(
                'img[src*="viva-images.com"]', state="visible", timeout=15000
            )
        except Exception:
            s("Thumbnails nao apareceram -- continuando sem imagens.")

        ad_list = _fetch_ad_list(page, account_id, access_token, s)
        if not ad_list:
            msg = "Nenhum anuncio encontrado."
            s(msg)
            if done_cb:
                done_cb(False, msg, [])
            return

        images = _extract_images_from_page(page)
        s(str(len(images)) + " thumbnail(s) extraida(s).")

        for ad in ad_list:
            ad_id = ad["id"]
            ads.append({
                "ad_id":     ad_id,
                "title":     ad["title"],
                "edit_url":  _EDIT_URL_TPL.format(ad_id=ad_id),
                "image_url": images.get(ad_id, ""),
            })

        msg = str(len(ads)) + " anuncio(s) carregado(s)."
        s(msg)
        if done_cb:
            done_cb(True, msg, ads)

    except Exception as exc:
        traceback.print_exc()
        msg = "Erro ao carregar anuncios: " + str(exc)
        s(msg)
        if done_cb:
            done_cb(False, msg, ads)

    finally:
        _close_cdp_session(pw, cdp_browser, page, page_was_created)
