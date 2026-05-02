import threading
from io import BytesIO

import tkinter as tk
from tkinter import ttk

import requests
from PIL import Image, ImageTk

from ui.edit_window import EditWindow


class AdPickerWindow(tk.Toplevel):
    def __init__(self, parent, ads: list[dict], pw_session):
        super().__init__(parent)
        self.title("Selecionar Anúncio")
        self.resizable(False, False)
        self.ads = ads
        self.pw_session = pw_session
        self.selected = tk.IntVar(value=0)
        self.photo_refs = []
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        tk.Label(
            self, text="Escolha o anúncio para editar:",
            font=("Helvetica", 12, "bold")
        ).pack(pady=(14, 8), padx=16)

        outer = tk.Frame(self)
        outer.pack(fill="both", expand=True, padx=12, pady=4)

        canvas = tk.Canvas(outer, width=420, height=min(320, len(self.ads) * 90 + 20))
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for i, ad in enumerate(self.ads):
            self._add_row(inner, i, ad)

        ttk.Button(
            self, text="✅  Editar este anúncio", command=self._on_select
        ).pack(pady=10)

    def _add_row(self, parent, idx: int, ad: dict):
        row = tk.Frame(parent, relief="groove", bd=1, padx=6, pady=6)
        row.pack(fill="x", padx=6, pady=4)

        ttk.Radiobutton(row, variable=self.selected, value=idx).pack(side="left")

        lbl_img = tk.Label(row, width=64, height=64, bg="#e8e8e8", text="…")
        lbl_img.pack(side="left", padx=6)

        info = tk.Frame(row)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=ad.get("name", "—"), font=("Helvetica", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(info, text=f"ID: {ad.get('escort_id', '')}", font=("Helvetica", 8), fg="gray", anchor="w").pack(fill="x")

        if ad.get("image_url"):
            threading.Thread(
                target=self._load_img, args=(ad["image_url"], lbl_img), daemon=True
            ).start()

    def _load_img(self, url: str, label: tk.Label):
        try:
            resp = requests.get(url, timeout=10)
            img = Image.open(BytesIO(resp.content)).convert("RGB").resize((64, 64), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.photo_refs.append(photo)
            self.after(0, lambda: label.config(image=photo, width=64, height=64, bg="white", text=""))
        except Exception:
            pass

    def _on_select(self):
        ad = self.ads[self.selected.get()]
        self.destroy()
        EditWindow(self.master, ad, self.pw_session)
