import queue
import threading
import traceback

from playwright.sync_api import sync_playwright

from config import LOGIN_URL, DASHBOARD_URL, USER_AGENT
from utils import ts

_READ_ADS_JS = """() => {
    const buttons = Array.from(document.querySelectorAll('button.editButton'))
        .filter(b => b.offsetParent !== null);
    const seen = new Set();
    const result = [];

    for (const btn of buttons) {
        const escortId = btn.dataset.escort || '';
        if (!escortId || seen.has(escortId)) continue;
        seen.add(escortId);

        const photoDiv = btn.closest('[class*="photo-edit"]') || btn.parentElement;
        const row      = btn.closest('.row') || photoDiv?.parentElement;
        const img      = photoDiv ? photoDiv.querySelector('img') : null;
        const rowText  = row ? row.innerText : '';

        const nameMatch = rowText.match(/Name:\\s*([^\\n]+)/);
        let imgSrc = img ? img.src : '';
        if (imgSrc.startsWith('//')) imgSrc = 'https:' + imgSrc;

        const boostBtnOk  = document.querySelector(
            `button.boostButton[data-escort="${escortId}"]:not([disabled])`);
        const boostBtnAny = document.querySelector(
            `button.boostButton[data-escort="${escortId}"]`);
        const countdownSpan = document.querySelector(
            `span.boost-countdown[data-escort="${escortId}"]`);

        let usedToday = '0';
        if (boostBtnAny) {
            const section = boostBtnAny.closest('.manual-boost-col, .profile-boost');
            const el = section ? section.querySelector('.boost-used-today') : null;
            if (el) usedToday = el.textContent.trim();
        }

        const countdown  = countdownSpan ? countdownSpan.textContent.trim() : '';
        const inCooldown = (/\\d+:\\d+/.test(countdown)) || (!boostBtnOk);

        result.push({
            escort_id:    escortId,
            image_url:    imgSrc,
            name:         nameMatch ? nameMatch[1].trim() : (escortId || 'Anúncio'),
            in_cooldown:  inCooldown,
            cooldown_time: countdown,
            used_today:   usedToday,
        });
    }
    return result;
}"""


class PlaywrightSession:
    _CMD_BOOST   = "boost"
    _CMD_REFRESH = "refresh"
    _CMD_STOP    = "stop"
    _CMD_UPDATE  = "update"

    def __init__(self):
        self._q      = queue.Queue()
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

    def boost(self, escort_id: str, status_cb=None, done_cb=None):
        self._q.put((self._CMD_BOOST, (escort_id, status_cb, done_cb)))

    def refresh(self, done_cb=None):
        self._q.put((self._CMD_REFRESH, done_cb))

    def update(self, edit_url: str, fn_or_text, status_cb=None, done_cb=None):
        """Atualiza a descrição de um anúncio.

        fn_or_text: callable(desc_atual) -> nova_desc, ou str para substituição direta.
        """
        self._q.put((self._CMD_UPDATE, (edit_url, fn_or_text, status_cb, done_cb)))

    def stop(self):
        self._q.put((self._CMD_STOP, None))

    def _worker(self, user: str, password: str, status_cb, done_cb):
        def s(msg):
            print(f"{ts()} [LOGIN] {msg}", flush=True)
            if status_cb:
                status_cb(msg)

        pw = browser = None
        try:
            pw      = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page    = context.new_page()

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
            page.fill("input[type='email']",    user)
            page.fill("input[type='password']", password)
            page.click("button[type='submit']")

            s("Aguardando redirecionamento…")
            page.wait_for_url(lambda url: "login" not in url, timeout=20000)
            s(f"Logado! URL: {page.url}")

            s("Carregando dashboard…")
            page.goto(DASHBOARD_URL, wait_until="networkidle")
            ads   = self._read_ads(page)
            total = len(ads)

            if done_cb:
                done_cb(True, f"Login OK! {total} anúncio(s) encontrado(s).", ads)

            while True:
                try:
                    cmd, payload = self._q.get(timeout=60)
                except queue.Empty:
                    continue

                if cmd == self._CMD_STOP:
                    break

                elif cmd == self._CMD_BOOST:
                    escort_id, upd_status_cb, upd_done_cb = payload
                    self._do_boost(page, escort_id, upd_status_cb, upd_done_cb)

                elif cmd == self._CMD_REFRESH:
                    refresh_done_cb = payload
                    try:
                        s("Atualizando dashboard…")
                        page.goto(DASHBOARD_URL, wait_until="networkidle")
                        ads = self._read_ads(page)
                        if refresh_done_cb:
                            refresh_done_cb(True, ads)
                    except Exception as e:
                        print(f"{ts()} [REFRESH] Erro: {e}", flush=True)
                        if refresh_done_cb:
                            refresh_done_cb(False, [])

                elif cmd == self._CMD_UPDATE:
                    edit_url, fn_or_text, upd_status_cb, upd_done_cb = payload
                    self._do_update(context, edit_url, fn_or_text, upd_status_cb, upd_done_cb)

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

    def _read_ads(self, page) -> list[dict]:
        return page.evaluate(_READ_ADS_JS)

    def _do_update(self, context, edit_url: str, fn_or_text, status_cb, done_cb):
        """Abre uma nova aba, edita a descrição do anúncio e fecha a aba."""
        def s(msg):
            print(f"{ts()} [UPDATE] {msg}", flush=True)
            if status_cb:
                status_cb(msg)

        new_page = None
        try:
            s("Abrindo página do anúncio…")
            new_page = context.new_page()
            new_page.goto(edit_url, wait_until="networkidle")

            # Lê a descrição atual
            current_desc = new_page.evaluate("""() => {
                const ta = document.querySelector('textarea[name="description"]');
                if (ta) return ta.value;
                const inp = document.querySelector('input[name="description"]');
                return inp ? inp.value : '';
            }""")

            # Calcula a nova descrição
            new_desc = fn_or_text(current_desc) if callable(fn_or_text) else fn_or_text

            s("Preenchendo nova descrição…")
            new_page.fill('textarea[name="description"]', new_desc)

            s("Salvando…")
            new_page.click('button[type="submit"]')
            new_page.wait_for_timeout(2000)

            if done_cb:
                done_cb(True, "✅ Descrição atualizada com sucesso!")

        except Exception as e:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"❌ Erro ao atualizar: {e}")
        finally:
            if new_page:
                try:
                    new_page.close()
                except Exception:
                    pass

    def _do_boost(self, page, escort_id: str, status_cb, done_cb):
        def s(msg):
            print(f"{ts()} [BOOST:{escort_id}] {msg}", flush=True)
            if status_cb:
                status_cb(msg)

        try:
            if DASHBOARD_URL not in page.url:
                s("Navegando ao dashboard…")
                page.goto(DASHBOARD_URL, wait_until="networkidle")

            s("Verificando disponibilidade do boost…")
            in_cooldown = page.evaluate(f"""() => {{
                const countdownSpan = document.querySelector(
                    "span.boost-countdown[data-escort='{escort_id}']");
                const countdown = countdownSpan ? countdownSpan.textContent.trim() : '';
                const boostBtnOk = document.querySelector(
                    "button.boostButton[data-escort='{escort_id}']:not([disabled])");
                return (/\\d+:\\d+/.test(countdown)) || (!boostBtnOk);
            }}""")

            if in_cooldown:
                if done_cb:
                    done_cb(False, "⏱️ Anúncio em cooldown — boost indisponível.", None)
                return

            boost_btn = page.locator(
                f"button.boostButton[data-escort='{escort_id}']"
            ).first

            s("Clicando em Manual Boost…")
            boost_btn.click()

            s("Aguardando atualização da página…")
            page.wait_for_timeout(3000)

            new_state = page.evaluate(f"""() => {{
                const boostBtnOk  = document.querySelector(
                    "button.boostButton[data-escort='{escort_id}']:not([disabled])");
                const boostBtnAny = document.querySelector(
                    "button.boostButton[data-escort='{escort_id}']");
                const countdownSpan = document.querySelector(
                    "span.boost-countdown[data-escort='{escort_id}']");

                let usedToday = '0';
                if (boostBtnAny) {{
                    const section = boostBtnAny.closest('.manual-boost-col, .profile-boost');
                    const el = section ? section.querySelector('.boost-used-today') : null;
                    if (el) usedToday = el.textContent.trim();
                }}

                const countdown  = countdownSpan ? countdownSpan.textContent.trim() : '';
                const inCooldown = (/\\d+:\\d+/.test(countdown)) || (!boostBtnOk);

                return {{
                    in_cooldown:   inCooldown,
                    cooldown_time: countdown,
                    used_today:    usedToday,
                }};
            }}""")

            if done_cb:
                done_cb(True, "✅ Boost realizado!", new_state)

        except Exception as e:
            traceback.print_exc()
            if done_cb:
                done_cb(False, f"❌ Erro ao fazer boost: {e}", None)
