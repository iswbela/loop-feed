import tkinter as tk
from tkinter import ttk, messagebox

from persistence import get_messages_for
from utils import simple_toggle
from ui.messages_window import MessagesWindow


class EditWindow(tk.Toplevel):
    def __init__(self, parent, ad: dict, pw_session):
        super().__init__(parent)
        self.title(f"Editar — {ad.get('name', '')}")
        self.resizable(False, False)
        self.ad = ad
        self.pw_session = pw_session
        self.mode = tk.StringVar(value="simple")
        self._build_ui()
        self.grab_set()

    def _build_ui(self):
        pad = {"padx": 16, "pady": 6}

        tk.Label(
            self,
            text=f"Anúncio: {self.ad.get('name', '')}",
            font=("Helvetica", 11, "bold")
        ).pack(anchor="w", **pad)

        tk.Label(
            self, text=f"URL: {self.ad.get('edit_url', '')}",
            font=("Helvetica", 8), fg="gray", wraplength=400, justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 8))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12)

        mode_frame = tk.LabelFrame(self, text="Modo de edição", padx=10, pady=8)
        mode_frame.pack(fill="x", padx=14, pady=10)

        ttk.Radiobutton(
            mode_frame, text="Simples  (alterna ❤️ no final da descrição)",
            variable=self.mode, value="simple", command=self._on_mode_change
        ).pack(anchor="w")

        ttk.Radiobutton(
            mode_frame, text="Personalizado  (escolhe mensagem da lista)",
            variable=self.mode, value="custom", command=self._on_mode_change
        ).pack(anchor="w", pady=(4, 0))

        self.panel_simple = tk.Frame(self)
        tk.Label(
            self.panel_simple,
            text="A cada execução, o ❤️ será adicionado ou removido\ndo final da descrição atual.",
            justify="left", fg="#555"
        ).pack(anchor="w", padx=16, pady=6)

        self.panel_custom = tk.Frame(self)

        tk.Label(
            self.panel_custom, text="Selecione a mensagem a aplicar:",
            font=("Helvetica", 9, "bold")
        ).pack(anchor="w", padx=14, pady=(6, 2))

        list_frame = tk.Frame(self.panel_custom)
        list_frame.pack(fill="x", padx=14)

        sb = ttk.Scrollbar(list_frame, orient="vertical")
        self.msg_listbox = tk.Listbox(
            list_frame, width=50, height=6,
            yscrollcommand=sb.set, selectmode="single"
        )
        sb.config(command=self.msg_listbox.yview)
        self.msg_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._reload_messages()

        ttk.Button(
            self.panel_custom, text="⚙️  Gerenciar mensagens",
            command=self._open_messages_manager
        ).pack(anchor="w", padx=14, pady=(6, 0))

        self.btn_update = ttk.Button(
            self, text="🔄  Atualizar descrição agora", command=self._on_update
        )
        self.btn_update.pack(pady=14, ipadx=8)

        self.lbl_status = tk.Label(
            self, text="", wraplength=420,
            justify="center", font=("Helvetica", 9)
        )
        self.lbl_status.pack(pady=(0, 12))

        self._on_mode_change()

    def _on_mode_change(self):
        self.panel_simple.pack_forget()
        self.panel_custom.pack_forget()
        if self.mode.get() == "simple":
            self.panel_simple.pack(fill="x", before=self.btn_update)
        else:
            self.panel_custom.pack(fill="x", before=self.btn_update)

    def _reload_messages(self):
        self.msg_listbox.delete(0, tk.END)
        for msg in get_messages_for(self.ad["escort_id"]):
            self.msg_listbox.insert(tk.END, msg)

    def _open_messages_manager(self):
        win = MessagesWindow(self, self.ad["escort_id"], self.ad.get("name", ""))
        self.wait_window(win)
        self._reload_messages()

    def _set_status(self, msg: str, color: str = "black"):
        self.lbl_status.config(text=msg, fg=color)

    def _on_update(self):
        mode = self.mode.get()

        if mode == "simple":
            fn = simple_toggle
        else:
            sel = self.msg_listbox.curselection()
            if not sel:
                messagebox.showinfo(
                    "Selecione uma mensagem",
                    "Escolha uma mensagem da lista antes de atualizar.",
                    parent=self
                )
                return
            fn = self.msg_listbox.get(sel[0])

        self.btn_update.config(state="disabled", text="Aguarde…")
        self._set_status("Enviando job de atualização…", "gray")

        def status_cb(msg):
            self.after(0, lambda: self._set_status(msg, "gray"))

        def done_cb(success, message):
            self.after(0, self._on_update_done, success, message)

        self.pw_session.update(
            self.ad.get("edit_url", ""), fn,
            status_cb=status_cb, done_cb=done_cb,
        )

    def _on_update_done(self, success: bool, message: str):
        self.btn_update.config(state="normal", text="🔄  Atualizar descrição agora")
        self._set_status(message, "green" if success else "red")
