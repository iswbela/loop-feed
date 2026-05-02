import os
import queue
import threading
import traceback

from playwright.sync_api import sync_playwright

from config import BASE_DIR, LOGIN_URL, DASHBOARD_URL, USER_AGENT
from utils import ts


class PlaywrightSession:
    _CMD_UPDATE = "update"
    _CMD_STOP = "stop"

    def __init__(self):
        self._q = queue.Queue()
        self._thread = None

    def login(self, user: str, password: str, status_cb=None, done_cb=None):
        if self._thread and self._thread.is_alive():
            self._q.put((self._CMD_STOP, None))

        self._thread = threading.Thread(
            target=self._worker,
            args=(user, password, status_cb, done_cb),
            daemon=True,
        )
        self._thread.start()

    def update(self, edit_url: str, description_fn, status_cb=None, done_cb=None):
        self._q.put((self._CMD_UPDATE, (edit_url, description_fn, status_cb, done_cb)))

    def stop(self):
        self._q.put((self._CMD_STOP, None))

    def _worker(self, user: str, password: str, status_cb, done_cb):
        def s(msg):
            print(f"{ts()} [LOGIN] {msg}", flush=True)
            if status_cb:
                status_cb(msg)

        pw = None
        browser = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            page.on("dialog", lambda d: (
                print(f"{ts()} [DIALOG] {d.type}: {d.message!r}"),
                d.accept(),
            ))

            s("Abrindo página de login…")
            page.goto(LOGIN_URL, wait_until="networkidle")

            s("Verificando modal de aviso…")
            try:
                page.wait_for_selector("#kommonsDisclaimer", state="visible", timeout=5000)
                for texto in ["Submit", "Accept", "Acknowledge", "Agree", "OK", "Close"]:
                    btn = page.locator(f"#kommonsDisclaimer button:has-text('{texto}')")
                    if btn.count() > 0:
                        btn.first.click()
                        page.wait_for_timeout(500)
                        s(f"Modal fechado ('{texto}').")
                        break
                else:
                    page.locator("#kommonsDisclaimer button").first.click()
                    page.wait_for_timeout(500)
            except Exception:
                s("Nenhum modal encontrado.")

            s("Preenchendo credenciais…")
            page.fill("input[type='email']", user)
            page.fill("input[type='password']", password)
            page.click("button[type='submit']")

            s("Aguardando redirecionamento…")
            page.wait_for_url(lambda url: "login" not in url, timeout=20000)
            s(f"Logado! URL: {page.url}")

            s("Buscando anúncios…")
            page.goto(DASHBOARD_URL, wait_until="networkidle")

            ads_raw = page.evaluate("""() => {
                const all = document.querySelectorAll('button.editButton');
                const buttons = Array.from(all).filter(btn => btn.offsetParent !== null);
                return buttons.map(btn => {
                    const photoDiv = btn.closest('[class*="photo-edit"]') || btn.parentElement;
                    const row = btn.closest('.row') || photoDiv?.parentElement;
                    const img = photoDiv ? photoDiv.querySelector('img') : null;
                    const rowText = row ? row.innerText : '';
                    const nameMatch = rowText.match(/Name:\\s*([^\\n]+)/);
                    let imgSrc = img ? img.src : '';
                    if (imgSrc.startsWith('//')) imgSrc = 'https:' + imgSrc;
                    return {
                        escort_id: btn.dataset.escort || '',
                        image_url: imgSrc,
                        name: nameMatch ? nameMatch[1].trim() : (btn.dataset.escort || 'Anúncio'),
                        edit_url: ''
                    };
                });
            }""")

            total = len(ads_raw)
            for i, ad in enumerate(ads_raw):
                try:
                    s(f"Capturando URL do anúncio {i + 1}/{total}…")
                    page.locator(f"button.editButton[data-escort='{ad['escort_id']}']").first.click()
                    page.wait_for_load_state("networkidle", timeout=10000)
                    ad["edit_url"] = page.url
                    page.go_back()
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception as e:
                    print(f"{ts()} [WARN] URL não capturada para {ad['escort_id']}: {e}")
                    ad["edit_url"] = ""

            if done_cb:
                done_cb(True, f"Login OK! {total} anúncio(s) encontrado(s).", ads_raw)

            while True:
                try:
                    cmd, payload = self._q.get(timeout=60)
                except queue.Empty:
                    continue

                if cmd == self._CMD_STOP:
                    break

                if cmd == self._CMD_UPDATE:
                    edit_url, desc_fn, upd_status_cb, upd_done_cb = payload
                    self._do_update(page, edit_url, desc_fn, upd_status_cb, upd_done_cb)

        except Exception as e:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"Erro durante o login: {e}", [])
        finally:
            try:
                if browser:
                    browser.close()
                if pw:
                    pw.stop()
            except Exception:
                pass

    def _do_update(self, page, edit_url: str, description_fn, status_cb, done_cb):
        def s(msg):
            print(f"{ts()} [UPDATE] {msg}", flush=True)
            if status_cb:
                status_cb(msg)

        try:
            s("Abrindo página de edição…")
            page.goto(edit_url, wait_until="networkidle", timeout=30000)
            print(f"{ts()} [UPDATE] URL após navegação: {page.url}")

            if "login" in page.url:
                if done_cb:
                    done_cb(False, "❌ Sessão expirada. Refaça o login.")
                return

            s("Aguardando formulário…")
            try:
                page.wait_for_selector("#inputDescription", state="visible", timeout=15000)
            except Exception:
                debug_path = os.path.join(BASE_DIR, "debug_edit_page.html")
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
                forms = page.evaluate(
                    "Array.from(document.querySelectorAll('form')).map(f => f.id)"
                )
                print(f"{ts()} [UPDATE] Textarea não encontrada. Forms: {forms}. HTML → {debug_path}")
                if done_cb:
                    done_cb(False, f"❌ Formulário não encontrado. Forms: {forms}")
                return

            s("Lendo descrição atual…")
            current = page.evaluate("document.getElementById('inputDescription').value") or ""
            print(f"{ts()} [UPDATE] Descrição atual: {len(current)} chars")

            new_desc = description_fn(current) if callable(description_fn) else description_fn
            print(f"{ts()} [UPDATE] Nova descrição:  {len(new_desc)} chars")

            if new_desc == current:
                if done_cb:
                    done_cb(False, "⚠️ Descrição idêntica à atual — nada a salvar.")
                return

            s("Preenchendo nova descrição…")
            page.evaluate(
                "(v) => { document.getElementById('inputDescription').value = v; }",
                new_desc,
            )

            s("Clicando em Salvar…")
            page.evaluate("document.getElementById('escortAdButton').click()")

            s("Aguardando confirmação…")
            page.wait_for_timeout(4000)
            print(f"{ts()} [UPDATE] URL após salvar: {page.url}")

            if done_cb:
                done_cb(True, "✅ Descrição atualizada com sucesso!")

        except Exception as e:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"❌ Erro ao atualizar: {e}")
