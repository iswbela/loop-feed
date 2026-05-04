import os
import tkinter as tk
from tkinter import ttk, messagebox

from config import CONFIG_FILE
from persistence import load_credentials, save_credentials
from browser.session import PlaywrightSession
from ui.boost_window import BoostWindow


class KommonsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kommons – Loop Feed")
        self.resizable(False, False)
        self.configure(padx=30, pady=24)
        self.pw_session = PlaywrightSession()
        self._build_ui()
        self._load_saved_credentials()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
            command=lambda: self.entry_pass.config(show="" if self.show_pass.get() else "•")
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

    def _set_status(self, msg: str, color: str = "black"):
        self.lbl_status.config(text=msg, fg=color)

    def _load_saved_credentials(self):
        creds = load_credentials()
        if creds:
            self.entry_user.insert(0, creds.get("user", ""))
            self.entry_pass.insert(0, creds.get("password", ""))
            self.save_creds.set(True)

    def _on_login(self):
        user = self.entry_user.get().strip()
        password = self.entry_pass.get()
        if not user or not password:
            messagebox.showwarning("Campos obrigatórios", "Preencha e-mail e senha.")
            return

        if self.save_creds.get():
            save_credentials(user, password)
        elif os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

        self.btn_login.config(state="disabled", text="Aguarde…")
        self._set_status("Iniciando…", "gray")

        def status_cb(msg):
            self.after(0, lambda: self._set_status(msg, "gray"))

        def done_cb(success, message, ads):
            self.after(0, self._on_login_done, success, message, ads)

        self.pw_session.login(user, password, status_cb=status_cb, done_cb=done_cb)

    def _on_login_done(self, success: bool, message: str, ads: list):
        self.btn_login.config(state="normal", text="Entrar")
        if success:
            self._set_status(f"✅ {message}", "green")
            if ads:
                BoostWindow(self, ads, self.pw_session)
            else:
                self._set_status("✅ Login OK, mas nenhum anúncio encontrado.", "orange")
        else:
            self._set_status(f"❌ {message}", "red")

    def _on_close(self):
        self.pw_session.stop()
        self.destroy()
