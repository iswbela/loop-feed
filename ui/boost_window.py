import threading
from io import BytesIO

import tkinter as tk
from tkinter import ttk

import requests
from PIL import Image, ImageTk


class BoostWindow(tk.Toplevel):
    def __init__(self, parent, ads: list[dict], pw_session):
        super().__init__(parent)
        self.title("Loop Feed — Boost Manager")
        self.minsize(560, 400)
        self.resizable(True, True)

        self.ads        = ads
        self.pw_session = pw_session
        self.photo_refs = []
        self._widgets   = {}

        self._boost_queue: list[str] = []

        self._build_ui()
        self._load_all_images()
        self.grab_set()

    def _build_ui(self):
        top = tk.Frame(self, padx=14, pady=10)
        top.pack(fill="x")

        tk.Label(
            top, text="Boost Manager",
            font=("Helvetica", 14, "bold")
        ).pack(side="left")

        btn_frame = tk.Frame(top)
        btn_frame.pack(side="right")

        self.btn_refresh = ttk.Button(
            btn_frame, text="🔄  Atualizar",
            command=self._on_refresh, width=14
        )
        self.btn_refresh.pack(side="left", padx=(0, 8))

        self.btn_boost_all = ttk.Button(
            btn_frame, text="🚀  Boost em Todos",
            command=self._on_boost_all, width=18
        )
        self.btn_boost_all.pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        outer = tk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        canvas_h = min(440, len(self.ads) * 96 + 20) if self.ads else 200
        self._canvas = tk.Canvas(outer, width=540, height=canvas_h)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)

        self._inner = tk.Frame(self._canvas)
        self._inner.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        if self.ads:
            for ad in self.ads:
                self._add_ad_row(ad)
        else:
            tk.Label(
                self._inner, text="Nenhum anúncio encontrado.",
                fg="gray", pady=30
            ).pack()

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        self.lbl_status = tk.Label(
            self, text="Pronto.",
            wraplength=540, justify="center",
            font=("Helvetica", 9), padx=12, pady=6
        )
        self.lbl_status.pack(fill="x")

    def _add_ad_row(self, ad: dict):
        escort_id = ad.get("escort_id", "")

        row = tk.Frame(self._inner, relief="groove", bd=1, padx=8, pady=8)
        row.pack(fill="x", padx=6, pady=4)

        lbl_img = tk.Label(row, width=64, height=64, bg="#e8e8e8", text="…")
        lbl_img.pack(side="left", padx=(0, 12))

        info = tk.Frame(row)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info,
            text=ad.get("name", "—"),
            font=("Helvetica", 10, "bold"),
            anchor="w"
        ).pack(fill="x")

        tk.Label(
            info,
            text=f"ID: {escort_id}",
            font=("Helvetica", 8),
            fg="gray",
            anchor="w"
        ).pack(fill="x")

        lbl_cooldown = tk.Label(info, text="", font=("Helvetica", 8), anchor="w")
        lbl_cooldown.pack(fill="x")

        btn = ttk.Button(row, text="⚡  Boost", width=12)
        btn.config(command=lambda eid=escort_id: self._on_boost(eid))
        btn.pack(side="right", padx=(12, 0))

        self._widgets[escort_id] = {
            "img_label":      lbl_img,
            "cooldown_label": lbl_cooldown,
            "boost_btn":      btn,
            "ad":             ad,
        }

        self._apply_state(escort_id, ad)

    def _apply_state(self, escort_id: str, ad: dict):
        w = self._widgets.get(escort_id)
        if not w:
            return

        btn        = w["boost_btn"]
        lbl        = w["cooldown_label"]
        in_cooldown   = ad.get("in_cooldown", False)
        cooldown_time = ad.get("cooldown_time", "")
        used_today    = ad.get("used_today", "0")

        if in_cooldown:
            btn.config(state="disabled")
            parts = [f"{used_today} usados hoje"]
            if cooldown_time:
                parts.append(f"⏱ {cooldown_time}")
            lbl.config(text="  •  ".join(parts), fg="#cc0000")
        else:
            btn.config(state="normal")
            if used_today and used_today != "0":
                lbl.config(text=f"{used_today} usados hoje", fg="gray")
            else:
                lbl.config(text="Disponível", fg="#1a7f1a")

    def _load_all_images(self):
        for ad in self.ads:
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
            self.photo_refs.append(photo)
            self.after(
                0,
                lambda: label.config(image=photo, width=64, height=64, bg="white", text="")
            )
        except Exception:
            pass

    def _set_status(self, msg: str, color: str = "black"):
        self.lbl_status.config(text=msg, fg=color)

    def _lock_global_buttons(self):
        self.btn_boost_all.config(state="disabled")
        self.btn_refresh.config(state="disabled")

    def _unlock_global_buttons(self):
        self.btn_boost_all.config(state="normal")
        self.btn_refresh.config(state="normal")
        for eid, w in self._widgets.items():
            if not w["ad"].get("in_cooldown", False):
                w["boost_btn"].config(state="normal")

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
                elif success:
                    w["boost_btn"].config(state="normal")
                self._set_status(message, "green" if success else "red")
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
            self._unlock_global_buttons()
            self._set_status("✅ Boost em todos concluído!", "green")
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
                color = "green" if success else "red"
                self._set_status(msg, color)
            self.after(0, update)

        self.pw_session.refresh(done_cb=done_cb)
