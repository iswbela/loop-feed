"""
ui/app.py — Janela principal do Loop Feed (sem login manual).

Fluxo de inicialização:
  1. Carrega contas Kommons salvas via AccountManager.
  2. Autentica automaticamente via PlaywrightSession (browser headless).
  3. Exibe diretamente a lista de anúncios com ações de boost.
  4. Botão "⚙️ Gerenciar Contas" abre o modal de contas a qualquer momento.

O comportamento de boost é mantido idêntico ao BoostWindow anterior.
"""

from __future__ import annotations

import threading
from io import BytesIO
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

import requests
from PIL import Image, ImageTk

from accounts import AccountManager, ACCOUNT_TYPE_KOMMONS
from browser.session import PlaywrightSession
from config import APP_VERSION
from ui.accounts_modal import AccountsModal
from ui.edit_window import EditWindow


# ---------------------------------------------------------------------------
# Paleta de cores
# ---------------------------------------------------------------------------

_BG_HEADER = "#1a1a2e"
_FG_HEADER = "#ffffff"
_BG_OK     = "#e8f5e9"
_BG_ERR    = "#ffebee"
_BG_WARN   = "#fff8e1"
_FG_OK     = "#2e7d32"
_FG_ERR    = "#c62828"
_FG_WARN   = "#f57f17"


# ---------------------------------------------------------------------------
# Aplicação principal
# ---------------------------------------------------------------------------

class KommonsApp(tk.Tk):
    """
    Janela principal do Loop Feed.

    Não exibe tela de login — autentica automaticamente a conta
    Kommons salva e mostra os anúncios com ações de boost diretamente.
    """

    def __init__(self):
        super().__init__()
        self.title("Loop Feed")
        self.minsize(560, 440)
        self.resizable(True, True)

        self._manager   = AccountManager()
        self.pw_session = PlaywrightSession()

        self._ads: list[dict] = []
        self._widgets: dict[str, dict] = {}
        self._boost_queue: list[str] = []

        self._build_ui()
        self.after(120, self._boot)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Cabeçalho ──────────────────────────────────────────────────
        header = tk.Frame(self, bg=_BG_HEADER)
        header.pack(fill="x")

        tk.Label(
            header,
            text=f"Loop Feed",
            font=("Helvetica", 14, "bold"),
            bg=_BG_HEADER,
            fg=_FG_HEADER,
        ).pack(side="left", padx=(16, 4), pady=10)

        tk.Label(
            header,
            text=f"v{APP_VERSION}",
            font=("Helvetica", 8),
            bg=_BG_HEADER,
            fg="#8888aa",
        ).pack(side="left", pady=10)

        btn_bar = tk.Frame(header, bg=_BG_HEADER)
        btn_bar.pack(side="right", padx=12, pady=8)

        ttk.Button(
            btn_bar, text="⚙️  Gerenciar Contas", command=self._open_accounts
        ).pack(side="left", padx=4)

        self.btn_refresh = ttk.Button(
            btn_bar, text="🔄  Atualizar", command=self._on_refresh, width=12
        )
        self.btn_refresh.pack(side="left", padx=4)

        self.btn_boost_all = ttk.Button(
            btn_bar, text="🚀  Boost em Todos", command=self._on_boost_all, width=18
        )
        self.btn_boost_all.pack(side="left", padx=4)

        # Desabilitados até login concluir
        self.btn_refresh.config(state="disabled")
        self.btn_boost_all.config(state="disabled")

        # ── Barra de status de autenticação ────────────────────────────
        self._status_frame = tk.Frame(self, bg=_BG_WARN, pady=4)
        self._status_frame.pack(fill="x")

        self._lbl_auth = tk.Label(
            self._status_frame,
            text="⏳  Iniciando…",
            font=("Helvetica", 9),
            bg=_BG_WARN,
            fg=_FG_WARN,
            anchor="w",
        )
        self._lbl_auth.pack(fill="x", padx=16)

        # ── Área de anúncios com scroll ─────────────────────────────────
        outer = tk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        self._canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)

        self._inner = tk.Frame(self._canvas)
        self._inner.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._inner, anchor="nw"
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width),
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )

        # ── Rodapé de status de operações ──────────────────────────────
        footer = tk.Frame(self, bd=1, relief="sunken")
        footer.pack(fill="x", side="bottom")

        self.lbl_status = tk.Label(
            footer,
            text="",
            wraplength=540,
            justify="left",
            font=("Helvetica", 9),
            padx=12,
            pady=6,
        )
        self.lbl_status.pack(fill="x")

    # ------------------------------------------------------------------
    # Boot: autenticação automática
    # ------------------------------------------------------------------

    def _boot(self):
        """Carrega conta salva e inicia autenticação sem interação do usuário."""
        accounts = self._manager.list_by_type(ACCOUNT_TYPE_KOMMONS)

        if not accounts:
            self._set_auth_status(
                '⚠️  Nenhuma conta Kommons cadastrada. '
                'Use "⚙️ Gerenciar Contas" para adicionar.',
                kind="warn",
            )
            self._show_center('Adicione uma conta Kommons para começar.')
            return

        acc = accounts[0]
        self._set_auth_status("⏳  Autenticando…", kind="warn")
        self._show_center("Autenticando…")

        def status_cb(msg):
            self.after(0, lambda: self._set_status(msg, "gray"))

        def done_cb(success, message, ads):
            self.after(0, self._on_login_done, success, message, ads)

        self.pw_session.login(
            acc.username, acc.password,
            status_cb=status_cb, done_cb=done_cb,
        )

    def _on_login_done(self, success: bool, message: str, ads: list):
        if success:
            self._set_auth_status("✅  Kommons: conectado", kind="ok")
            self._set_status(f"✅ {message}", "green")
            self.btn_refresh.config(state="normal")
            self.btn_boost_all.config(state="normal")
            self._ads = ads or []
            self._widgets.clear()
            self._render_ads()
        else:
            self._set_auth_status(
                '❌  Falha na autenticação. Verifique suas contas em "⚙️ Gerenciar Contas".',
                kind="error",
            )
            self._show_center("Não foi possível autenticar.")
            self._set_status(f"❌ {message}", "red")

    # ------------------------------------------------------------------
    # Renderização dos anúncios
    # ------------------------------------------------------------------

    def _render_ads(self):
        for w in self._inner.winfo_children():
            w.destroy()

        if not self._ads:
            tk.Label(
                self._inner,
                text="Nenhum anúncio encontrado nesta conta.",
                fg="gray",
                pady=30,
            ).pack()
            return

        for ad in self._ads:
            self._add_ad_row(ad)

        self._load_all_images()

    def _add_ad_row(self, ad: dict):
        escort_id = ad.get("escort_id", "")

        row = tk.Frame(self._inner, relief="groove", bd=1, padx=8, pady=8)
        row.pack(fill="x", padx=6, pady=4)

        # Thumbnail
        lbl_img = tk.Label(row, width=64, height=64, bg="#e8e8e8", text="…")
        lbl_img.pack(side="left", padx=(0, 12))

        # Info textual
        info = tk.Frame(row)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info,
            text=ad.get("name", "—"),
            font=("Helvetica", 10, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            info,
            text=f"ID: {escort_id}",
            font=("Helvetica", 8),
            fg="gray",
            anchor="w",
        ).pack(fill="x")

        lbl_cooldown = tk.Label(info, text="", font=("Helvetica", 8), anchor="w")
        lbl_cooldown.pack(fill="x")

        # Botões de ação
        btn_col = tk.Frame(row)
        btn_col.pack(side="right", padx=(12, 0))

        btn_boost = ttk.Button(
            btn_col, text="⚡  Boost", width=12,
            command=lambda eid=escort_id: self._on_boost(eid),
        )
        btn_boost.pack(pady=(0, 4))

        ttk.Button(
            btn_col, text="✏️  Editar", width=12,
            command=lambda a=ad: EditWindow(self, a, self.pw_session),
        ).pack()

        self._widgets[escort_id] = {
            "img_label":      lbl_img,
            "cooldown_label": lbl_cooldown,
            "boost_btn":      btn_boost,
            "ad":             ad,
        }

        self._apply_state(escort_id, ad)

    def _apply_state(self, escort_id: str, ad: dict):
        w = self._widgets.get(escort_id)
        if not w:
            return

        in_cooldown   = ad.get("in_cooldown", False)
        cooldown_time = ad.get("cooldown_time", "")
        used_today    = ad.get("used_today", "0")

        if in_cooldown:
            w["boost_btn"].config(state="disabled")
            parts = [f"{used_today} usados hoje"]
            if cooldown_time:
                parts.append(f"⏱ {cooldown_time}")
            w["cooldown_label"].config(text="  •  ".join(parts), fg="#cc0000")
        else:
            w["boost_btn"].config(state="normal")
            if used_today and used_today != "0":
                w["cooldown_label"].config(text=f"{used_today} usados hoje", fg="gray")
            else:
                w["cooldown_label"].config(text="Disponível", fg="#1a7f1a")

    def _load_all_images(self):
        for ad in self._ads:
            if ad.get("image_url"):
                eid = ad.get("escort_id", "")
                w   = self._widgets.get(eid)
                if w:
                    threading.Thread(
                        target=self._fetch_img,
                        args=(ad["image_url"], w["img_label"]),
                        daemon=True,
                    ).start()

    def _fetch_img(self, url: str, label: tk.Label):
        try:
            resp  = requests.get(url, timeout=10)
            img   = Image.open(BytesIO(resp.content)).convert("RGB").resize(
                (64, 64), Image.LANCZOS
            )
            photo = ImageTk.PhotoImage(img)
            # Mantém referência para evitar garbage collection
            label._photo = photo  # type: ignore[attr-defined]
            self.after(
                0,
                lambda lbl=label, p=photo: lbl.config(
                    image=p, width=64, height=64, bg="white", text=""
                ),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Ações de boost (mantidas idênticas ao BoostWindow)
    # ------------------------------------------------------------------

    def _on_boost(self, escort_id: str):
        w = self._widgets.get(escort_id)
        if not w:
            return

        w["boost_btn"].config(state="disabled")
        self._set_status(f"Boostando anúncio {escort_id}…", "gray")

        def status_cb(msg):
            self.after(0, lambda: self._set_status(msg, "gray"))

        def done_cb(success, message, new_state):
            def update():
                if new_state:
                    w["ad"].update(new_state)
                    self._apply_state(escort_id, w["ad"])
                self._set_status(message, "green" if success else "red")
                # Atualiza o estado de todos os anúncios automaticamente
                if success:
                    self._on_refresh()
            self.after(0, update)

        self.pw_session.boost(escort_id, status_cb=status_cb, done_cb=done_cb)

    def _on_boost_all(self):
        available = [
            eid for eid, w in self._widgets.items()
            if not w["ad"].get("in_cooldown", False)
        ]
        if not available:
            self._set_status("Todos os anúncios estão em cooldown — nada a fazer.", "orange")
            return

        self._lock_global_buttons()
        self._set_status(f"Iniciando boost em {len(available)} anúncio(s)…", "gray")
        self._boost_queue = available
        self._run_next_in_queue()

    def _run_next_in_queue(self):
        if not self._boost_queue:
            self._set_status("✅ Boost em todos concluído! Atualizando…", "green")
            # Atualiza o estado de todos os anúncios automaticamente
            self._on_refresh()
            return

        escort_id = self._boost_queue.pop(0)
        name      = self._widgets.get(escort_id, {}).get("ad", {}).get("name", escort_id)
        self._set_status(f"Boostando {name}…", "gray")

        def done_cb(success, message, new_state):
            def update():
                w = self._widgets.get(escort_id)
                if w and new_state:
                    w["ad"].update(new_state)
                    self._apply_state(escort_id, w["ad"])
                self._run_next_in_queue()
            self.after(0, update)

        self.pw_session.boost(escort_id, done_cb=done_cb)

    def _on_refresh(self):
        self._lock_global_buttons()
        for w in self._widgets.values():
            w["boost_btn"].config(state="disabled")
        self._set_status("Atualizando estado dos anúncios…", "gray")

        def done_cb(success, ads):
            def update():
                if success and ads:
                    for ad in ads:
                        eid = ad.get("escort_id", "")
                        if eid in self._widgets:
                            self._widgets[eid]["ad"].update(ad)
                            self._apply_state(eid, ad)
                self._unlock_global_buttons()
                msg   = "✅ Estado atualizado!" if success else "❌ Falha ao atualizar."
                self._set_status(msg, "green" if success else "red")
            self.after(0, update)

        self.pw_session.refresh(done_cb=done_cb)

    def _lock_global_buttons(self):
        self.btn_boost_all.config(state="disabled")
        self.btn_refresh.config(state="disabled")

    def _unlock_global_buttons(self):
        self.btn_boost_all.config(state="normal")
        self.btn_refresh.config(state="normal")
        for eid, w in self._widgets.items():
            if not w["ad"].get("in_cooldown", False):
                w["boost_btn"].config(state="normal")

    # ------------------------------------------------------------------
    # Gerenciamento de contas
    # ------------------------------------------------------------------

    def _open_accounts(self):
        AccountsModal(self, on_change=self._on_accounts_changed)

    def _on_accounts_changed(self):
        """Reinicia a sessão quando o usuário altera contas."""
        self.pw_session.stop()
        self.pw_session = PlaywrightSession()
        self._widgets.clear()
        self._ads.clear()
        self.btn_refresh.config(state="disabled")
        self.btn_boost_all.config(state="disabled")
        self._set_auth_status("⏳  Reconectando…", kind="warn")
        self._show_center("Reconectando…")
        self.after(500, self._boot)

    # ------------------------------------------------------------------
    # Helpers visuais
    # ------------------------------------------------------------------

    def _set_auth_status(self, msg: str, kind: str = "warn"):
        palettes = {
            "ok":    (_BG_OK,   _FG_OK),
            "error": (_BG_ERR,  _FG_ERR),
            "warn":  (_BG_WARN, _FG_WARN),
        }
        bg, fg = palettes.get(kind, (_BG_WARN, _FG_WARN))
        self._status_frame.config(bg=bg)
        self._lbl_auth.config(text=msg, bg=bg, fg=fg)

    def _set_status(self, msg: str, color: str = "black"):
        self.lbl_status.config(text=msg, fg=color)

    def _show_center(self, msg: str):
        for w in self._inner.winfo_children():
            w.destroy()
        tk.Label(
            self._inner,
            text=msg,
            font=("Helvetica", 10),
            fg="gray",
            justify="center",
        ).pack(pady=40)

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------

    def _on_close(self):
        self.pw_session.stop()
        self.destroy()
