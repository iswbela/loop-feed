"""
ui/accounts_modal.py — Modal de gerenciamento de contas externas.

Permite adicionar, editar e remover contas do Kommons e VivaStreet.
Acessível a partir da janela principal via botão "⚙️ Gerenciar Contas".
"""

import tkinter as tk
from tkinter import ttk, messagebox

from accounts import Account, AccountManager, ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS


# ---------------------------------------------------------------------------
# Diálogo de formulário (adicionar / editar conta)
# ---------------------------------------------------------------------------

class _AccountFormDialog(tk.Toplevel):
    """
    Diálogo modal para criação ou edição de uma conta.

    Após fechar, verifique `.result` (Account ou None).
    """

    def __init__(self, parent, title: str, account: Account | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: Account | None = None
        self._account = account
        self._build_ui()
        self.grab_set()
        self.wait_window()

    def _build_ui(self):
        pad = {"padx": 16, "pady": 6}

        # Tipo da conta
        tk.Label(self, text="Tipo de conta:", anchor="w").grid(
            row=0, column=0, sticky="w", **pad
        )
        self._type_var = tk.StringVar(
            value=self._account.type if self._account else ACCOUNT_TYPES[0]
        )
        type_cb = ttk.Combobox(
            self,
            textvariable=self._type_var,
            values=[ACCOUNT_TYPE_LABELS[t] for t in ACCOUNT_TYPES],
            state="readonly",
            width=20,
        )
        # Mapeia label → chave interna ao mudar
        self._type_cb = type_cb
        type_cb.grid(row=0, column=1, sticky="w", **pad)
        if self._account:
            idx = ACCOUNT_TYPES.index(self._account.type) if self._account.type in ACCOUNT_TYPES else 0
            type_cb.current(idx)
        else:
            type_cb.current(0)
        type_cb.bind("<<ComboboxSelected>>", self._on_type_change)

        # Usuário / e-mail
        tk.Label(self, text="Usuário / e-mail:", anchor="w").grid(
            row=1, column=0, sticky="w", **pad
        )
        self._username_var = tk.StringVar(
            value=self._account.username if self._account else ""
        )
        ttk.Entry(self, textvariable=self._username_var, width=30).grid(
            row=1, column=1, sticky="w", **pad
        )

        # Senha
        tk.Label(self, text="Senha:", anchor="w").grid(
            row=2, column=0, sticky="w", **pad
        )
        self._password_var = tk.StringVar(
            value=self._account.password if self._account else ""
        )
        self._pw_entry = ttk.Entry(
            self, textvariable=self._password_var, show="•", width=30
        )
        self._pw_entry.grid(row=2, column=1, sticky="w", **pad)

        # Mostrar / ocultar senha
        self._show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Mostrar senha",
            variable=self._show_pw,
            command=self._toggle_pw,
        ).grid(row=3, column=1, sticky="w", padx=16, pady=(0, 4))

        # Rótulo (opcional)
        tk.Label(self, text="Rótulo (opcional):", anchor="w").grid(
            row=4, column=0, sticky="w", **pad
        )
        self._label_var = tk.StringVar(
            value=self._account.label if self._account else ""
        )
        ttk.Entry(self, textvariable=self._label_var, width=30).grid(
            row=4, column=1, sticky="w", **pad
        )

        ttk.Separator(self, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=6
        )

        # Botões
        btn_frame = tk.Frame(self)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=(0, 12))
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(6, 16)
        )
        ttk.Button(btn_frame, text="Salvar", command=self._on_save).pack(
            side="right", padx=4
        )

    def _on_type_change(self, _event=None):
        pass  # extensível no futuro

    def _toggle_pw(self):
        self._pw_entry.config(show="" if self._show_pw.get() else "•")

    def _on_save(self):
        # Mapeia label do combobox de volta para chave interna
        label_selected = self._type_var.get()
        label_to_key = {v: k for k, v in ACCOUNT_TYPE_LABELS.items()}
        type_key = label_to_key.get(label_selected, ACCOUNT_TYPES[0])

        username = self._username_var.get().strip()
        password = self._password_var.get().strip()

        if not username:
            messagebox.showwarning("Campo obrigatório", "Informe o usuário / e-mail.", parent=self)
            return
        if not password:
            messagebox.showwarning("Campo obrigatório", "Informe a senha.", parent=self)
            return

        self.result = Account(
            id=self._account.id if self._account else AccountManager.new_id(),
            type=type_key,
            username=username,
            password=password,
            label=self._label_var.get().strip(),
        )
        self.destroy()


# ---------------------------------------------------------------------------
# Modal principal de gerenciamento
# ---------------------------------------------------------------------------

class AccountsModal(tk.Toplevel):
    """
    Modal que lista todas as contas cadastradas e permite:
        - Adicionar nova conta
        - Editar conta existente
        - Remover conta

    Parâmetro `on_change` é chamado (sem argumentos) após qualquer alteração,
    permitindo que a janela pai reaja (re-autenticar, atualizar status, etc.).
    """

    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.title("Gerenciar Contas")
        self.resizable(False, False)
        self._manager = AccountManager()
        self._on_change = on_change
        self._build_ui()
        self._reload_list()
        self.grab_set()

    # ------------------------------------------------------------------
    # Construção da UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        tk.Label(
            self,
            text="Contas cadastradas",
            font=("Helvetica", 12, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 4))

        tk.Label(
            self,
            text="As credenciais são armazenadas localmente neste computador.",
            font=("Helvetica", 8),
            fg="gray",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        # Lista
        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=4)

        sb = ttk.Scrollbar(frame, orient="vertical")
        self._listbox = tk.Listbox(
            frame,
            width=52,
            height=10,
            yscrollcommand=sb.set,
            selectmode="single",
            font=("Courier", 10),
        )
        sb.config(command=self._listbox.yview)
        self._listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._listbox.bind("<Double-Button-1>", lambda _e: self._edit())

        # Botões de ação
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=8)

        ttk.Button(btn_frame, text="➕  Adicionar", command=self._add).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="✏️  Editar", command=self._edit).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="🗑  Remover", command=self._remove).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Fechar", command=self.destroy).pack(
            side="right", padx=4
        )

    # ------------------------------------------------------------------
    # Dados
    # ------------------------------------------------------------------

    def _reload_list(self):
        self._accounts = self._manager.load()
        self._listbox.delete(0, tk.END)
        for acc in self._accounts:
            tag = f"[{acc.type_label():<12}]"
            line = f"{tag}  {acc.display_name()}"
            self._listbox.insert(tk.END, line)

    def _selected_account(self):
        sel = self._listbox.curselection()
        if not sel:
            return None
        return self._accounts[sel[0]]

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------

    def _add(self):
        dlg = _AccountFormDialog(self, "Adicionar conta")
        if dlg.result:
            self._manager.add(dlg.result)
            self._reload_list()
            if self._on_change:
                self._on_change()

    def _edit(self):
        acc = self._selected_account()
        if not acc:
            messagebox.showinfo(
                "Selecione", "Selecione uma conta para editar.", parent=self
            )
            return
        dlg = _AccountFormDialog(self, "Editar conta", account=acc)
        if dlg.result:
            self._manager.update(dlg.result)
            self._reload_list()
            if self._on_change:
                self._on_change()

    def _remove(self):
        acc = self._selected_account()
        if not acc:
            messagebox.showinfo(
                "Selecione", "Selecione uma conta para remover.", parent=self
            )
            return
        if messagebox.askyesno(
            "Remover conta",
            f"Remover a conta «{acc.display_name()}» ({acc.type_label()})?",
            parent=self,
        ):
            self._manager.remove(acc.id)
            self._reload_list()
            if self._on_change:
                self._on_change()
