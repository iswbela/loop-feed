import tkinter as tk
from tkinter import ttk, messagebox
import requests
import json
import os
import threading

from playwright.sync_api import sync_playwright

CONFIG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kommons_config.json")
BASE_URL      = "https://kommons.com"
LOGIN_URL     = f"{BASE_URL}/members-area/login"
DASHBOARD_URL = f"{BASE_URL}/members-area"


def save_credentials(user: str, password: str) -> None:
    data = {"user": user, "password": password}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_credentials() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def do_login(user: str, password: str, status_cb=None) -> tuple[bool, str, requests.Session | None]:
    """
    1. Abre Chromium headless via Playwright (browser próprio, independente do sistema)
    2. Faz login no Kommons
    3. Extrai todos os cookies de sessão
    4. Injeta no requests.Session e retorna
    """
    def status(msg):
        if status_cb:
            status_cb(msg)

    try:
        with sync_playwright() as p:
            status("Iniciando navegador Chromium…")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            status("Abrindo página de login…")
            page.goto(LOGIN_URL, wait_until="networkidle")

            status("Verificando modal de aviso…")
            try:
                page.wait_for_selector("#kommonsDisclaimer", state="visible", timeout=5000)
                for texto in ["Submit", "Accept", "Acknowledge", "Agree", "OK", "Close", "Entendi", "I agree"]:
                    btn = page.locator(
                        f"#kommonsDisclaimer button:has-text('{texto}'), "
                        f"#kommonsDisclaimer a:has-text('{texto}')"
                    )
                    if btn.count() > 0:
                        btn.first.click()
                        page.wait_for_timeout(500)
                        status(f"Modal fechado (botão '{texto}').")
                        break
                else:
                    page.locator("#kommonsDisclaimer button").first.click()
                    page.wait_for_timeout(500)
                    status("Modal fechado (primeiro botão).")
            except Exception:
                status("Nenhum modal encontrado, seguindo…")

            status("Preenchendo e-mail…")
            page.fill("input[type='email']", user)

            status("Preenchendo senha…")
            page.fill("input[type='password']", password)

            status("Clicando em entrar…")
            page.click("button[type='submit']")

            status("Aguardando redirecionamento…")
            page.wait_for_url(lambda url: "login" not in url, timeout=20000)

            status("Capturando cookies de sessão…")
            pw_cookies = context.cookies()

            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            })
            for cookie in pw_cookies:
                session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))

            url_final = page.url
            browser.close()

            return True, f"Login realizado! ({len(pw_cookies)} cookies capturados)", session

    except Exception as e:
        return False, f"Erro durante o login: {e}", None


class KommonsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kommons – Loop Feed")
        self.resizable(False, False)
        self.configure(padx=30, pady=24)

        self.session: requests.Session | None = None
        self._build_ui()
        self._load_saved_credentials()

    def _build_ui(self):
        tk.Label(self, text="Kommons Login", font=("Helvetica", 16, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 18)
        )

        tk.Label(self, text="E-mail:", anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        self.entry_user = ttk.Entry(self, width=36)
        self.entry_user.grid(row=1, column=1, padx=(10, 0), pady=4)

        tk.Label(self, text="Senha:", anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        self.entry_pass = ttk.Entry(self, width=36, show="•")
        self.entry_pass.grid(row=2, column=1, padx=(10, 0), pady=4)

        self.show_pass = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Mostrar senha", variable=self.show_pass,
            command=self._toggle_password
        ).grid(row=3, column=1, sticky="w", padx=(10, 0))

        self.save_creds = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Lembrar credenciais", variable=self.save_creds
        ).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(0, 12))

        self.btn_login = ttk.Button(self, text="Entrar", command=self._on_login)
        self.btn_login.grid(row=5, column=0, columnspan=2, pady=8, ipadx=10)

        self.lbl_status = tk.Label(
            self, text="", wraplength=340, justify="center", font=("Helvetica", 9)
        )
        self.lbl_status.grid(row=6, column=0, columnspan=2, pady=(8, 0))

    def _toggle_password(self):
        self.entry_pass.config(show="" if self.show_pass.get() else "•")

    def _set_status(self, msg: str, color: str = "black"):
        self.lbl_status.config(text=msg, fg=color)

    def _load_saved_credentials(self):
        creds = load_credentials()
        if creds:
            self.entry_user.insert(0, creds.get("user", ""))
            self.entry_pass.insert(0, creds.get("password", ""))
            self.save_creds.set(True)

    def _on_login(self):
        user     = self.entry_user.get().strip()
        password = self.entry_pass.get()

        if not user or not password:
            messagebox.showwarning("Campos obrigatórios", "Preencha e-mail e senha.")
            return

        if self.save_creds.get():
            save_credentials(user, password)
        elif os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

        self.btn_login.config(state="disabled", text="Aguarde…")
        self._set_status("Abrindo navegador headless…", "gray")
        self.update()

        threading.Thread(target=self._run_login, args=(user, password), daemon=True).start()

    def _run_login(self, user: str, password: str):
        def status_cb(msg):
            self.after(0, lambda: self._set_status(msg, "gray"))

        success, message, session = do_login(user, password, status_cb=status_cb)
        self.after(0, self._on_login_done, success, message, session)

    def _on_login_done(self, success: bool, message: str, session):
        self.btn_login.config(state="normal", text="Entrar")
        if success:
            self.session = session
            self._set_status(f"✅ {message}", "green")
        else:
            self._set_status(f"❌ {message}", "red")


if __name__ == "__main__":
    app = KommonsApp()
    app.mainloop()
