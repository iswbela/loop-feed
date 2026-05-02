import tkinter as tk
from tkinter import ttk, messagebox

from persistence import get_messages_for, set_messages_for


class TextInputDialog(tk.Toplevel):
    def __init__(self, parent, title: str, prompt: str, initial_value: str = ""):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, False)
        self.result = None
        self._build(prompt, initial_value)
        self.grab_set()
        self.wait_window()

    def _build(self, prompt: str, initial_value: str):
        tk.Label(self, text=prompt, anchor="w").pack(anchor="w", padx=14, pady=(12, 4))

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        self.text = tk.Text(
            frame, width=50, height=6, wrap="word",
            yscrollcommand=scrollbar.set
        )
        scrollbar.config(command=self.text.yview)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        if initial_value:
            self.text.insert("1.0", initial_value)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=(4, 0))
        ttk.Button(btn_frame, text="OK", command=self._ok).pack(side="right")

        self.text.focus_set()
        self.bind("<Control-Return>", lambda e: self._ok())

    def _ok(self):
        self.result = self.text.get("1.0", "end-1c").strip()
        self.destroy()


class MessagesWindow(tk.Toplevel):
    def __init__(self, parent, escort_id: str, ad_name: str):
        super().__init__(parent)
        self.title(f"Mensagens — {ad_name}")
        self.resizable(False, False)
        self.escort_id = escort_id
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        tk.Label(
            self, text="Mensagens personalizadas:",
            font=("Helvetica", 10, "bold")
        ).pack(anchor="w", padx=14, pady=(12, 4))

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=4)

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        self.listbox = tk.Listbox(
            frame, width=52, height=10,
            yscrollcommand=scrollbar.set, selectmode="single"
        )
        scrollbar.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._reload_list()

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=8)

        ttk.Button(btn_frame, text="➕ Adicionar", command=self._add).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="✏️ Editar", command=self._edit).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🗑 Remover", command=self._remove).pack(side="left", padx=4)

    def _reload_list(self):
        self.listbox.delete(0, tk.END)
        for msg in get_messages_for(self.escort_id):
            self.listbox.insert(tk.END, msg)

    def _add(self):
        dialog = TextInputDialog(self, "Nova mensagem", "Digite a mensagem:")
        if dialog.result and dialog.result.strip():
            msgs = get_messages_for(self.escort_id)
            msgs.append(dialog.result.strip())
            set_messages_for(self.escort_id, msgs)
            self._reload_list()

    def _edit(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Selecione", "Selecione uma mensagem para editar.", parent=self)
            return
        idx = sel[0]
        msgs = get_messages_for(self.escort_id)
        dialog = TextInputDialog(
            self, "Editar mensagem", "Edite a mensagem:",
            initial_value=msgs[idx]
        )
        if dialog.result and dialog.result.strip():
            msgs[idx] = dialog.result.strip()
            set_messages_for(self.escort_id, msgs)
            self._reload_list()

    def _remove(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Selecione", "Selecione uma mensagem para remover.", parent=self)
            return
        idx = sel[0]
        msgs = get_messages_for(self.escort_id)
        if messagebox.askyesno("Remover", f"Remover: «{msgs[idx]}»?", parent=self):
            msgs.pop(idx)
            set_messages_for(self.escort_id, msgs)
            self._reload_list()
