"""Interface gráfica — Manutenção Epson (reset de almofadas, limpeza e teste de jatos)."""
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from usb_win import list_usb_printers, UsbPrinter, parse_device_id
import rede
from d4 import D4Link, CTRL, D4Error
from epson import (Epson, achar_modelo, carregar_modelos, salvar_backup, enviar_manutencao,
                   cmd_teste_jatos, cmd_limpeza, CONFIANCA, RECURSOS, AQUI,
                   LeituraRecusada, GravacaoRecusada, FalhaEnvio)

# Detalhes técnicos vão para registro.log (ao lado do .exe); a tela mostra só mensagens claras.
try:
    logging.basicConfig(filename=os.path.join(AQUI, "registro.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
except OSError:
    logging.basicConfig(level=logging.INFO)
log = logging.getLogger("manutencao")


def amigavel(e, acao):
    """Converte uma exceção em (título, mensagem) para o usuário."""
    if isinstance(e, GravacaoRecusada):
        if e.gravados == 0:
            return ("Reset não realizado",
                    "A impressora não aceitou a gravação. Nenhum contador foi alterado.\n\n"
                    "Este modelo pode ter o firmware bloqueado para reset.")
        return ("Reset incompleto",
                "O reset parou no meio (%d itens gravados). Os valores originais estão salvos na pasta "
                "'backups'.\n\nDesligue e ligue a impressora e tente o reset novamente." % e.gravados)
    if isinstance(e, LeituraRecusada):
        return ("Leitura não liberada",
                "A impressora não liberou a leitura dos contadores.\n\n"
                "O firmware pode estar bloqueado ou este modelo usa outro perfil. "
                "A limpeza e a verificação de jatos continuam disponíveis.")
    if isinstance(e, D4Error):
        if e.etapa == "entrar":
            return ("Impressora não respondeu",
                    "A impressora não entrou no modo de manutenção.\n\n"
                    "Verifique se ela está ligada, sem luz de erro piscando e sem impressão em andamento. "
                    "Se continuar, desligue e ligue a impressora.")
        return ("Comunicação interrompida",
                "A impressora parou de responder durante a operação.\n\n"
                "Verifique o cabo USB e se a impressora está ligada, depois clique em Detectar.")
    if isinstance(e, rede.SnmpErro):
        return ("Sem resposta pela rede",
                "A impressora não respondeu pela rede.\n\n"
                "Verifique se ela está ligada e na mesma rede Wi-Fi deste computador. "
                "Se o roteador isolar os aparelhos (rede de visitantes), use o cabo USB.")
    if isinstance(e, (ConnectionRefusedError, TimeoutError, ConnectionResetError)):
        return ("Sem conexão com a impressora",
                "Não foi possível enviar o comando pela rede.\n\n"
                "Verifique se a impressora está ligada e na mesma rede deste computador.")
    if isinstance(e, FalhaEnvio):
        return ("Comando não enviado",
                "Não foi possível enviar o comando à impressora.\n\n"
                "Verifique se há trabalhos travados na fila de impressão do Windows e se a impressora está ligada.")
    if isinstance(e, OSError):
        cod = getattr(e, "winerror", None) or e.errno
        if cod in (2, 3, 1167, 31, 433):
            return ("Impressora desconectada",
                    "A impressora foi desligada ou desconectada.\n\nReconecte o cabo USB e clique em Detectar.")
        if cod in (5, 13, 32, 170):
            return ("Impressora ocupada",
                    "A impressora está sendo usada por outro programa (impressão em andamento ou outro utilitário "
                    "aberto).\n\nAguarde ou feche o outro programa e tente de novo.")
    return ("Erro inesperado",
            "Não foi possível concluir: %s.\n\nOs detalhes técnicos foram salvos em 'registro.log'." % acao.lower())

AZUL = "#004966"
AZUL_CLARO = "#0A6A91"
AZUL_SUAVE = "#DCE9EF"
CINZA = "#666363"
CINZA_ESCURO = "#3F3D3D"
CINZA_CLARO = "#ECEDEE"
CINZA_LINHA = "#D5D6D7"
BRANCO = "#FFFFFF"
VERDE = "#2E8B57"
LARANJA = "#C77700"
VERMELHO = "#B3261E"
COR_CONF = {"A": VERDE, "B": AZUL_CLARO, "C": LARANJA, "D": VERMELHO}
NOMES = {"principal": "Almofada principal", "sem_margem": "Almofada sem margem", "terceiro": "Terceira almofada"}
FONTE = "Segoe UI"


class Botao(tk.Label):
    """Botão plano com hover."""

    def __init__(self, pai, texto, cmd, cor=AZUL, cor_hover=AZUL_CLARO, fg=BRANCO, **kw):
        super().__init__(pai, text=texto, bg=cor, fg=fg, cursor="hand2", padx=14, pady=10,
                         font=(FONTE, 10, "bold"), **kw)
        self.cor, self.cor_hover, self.fg, self.cmd, self.ativo = cor, cor_hover, fg, cmd, True
        self.bind("<Enter>", lambda e: self.ativo and self.config(bg=self.cor_hover))
        self.bind("<Leave>", lambda e: self.config(bg=self.cor if self.ativo else CINZA_LINHA))
        self.bind("<Button-1>", lambda e: self.ativo and self.cmd())

    def habilitar(self, sim=True):
        self.ativo = sim
        self.config(bg=self.cor if sim else CINZA_LINHA, fg=self.fg if sim else "#A3A1A1",
                    cursor="hand2" if sim else "arrow")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Manutenção Epson")
        self.geometry("820x690")
        self.minsize(760, 650)
        self.configure(bg=CINZA_CLARO)
        try:
            self.iconbitmap(os.path.join(RECURSOS, "icone.ico"))
        except Exception:
            pass
        self.fila = queue.Queue()
        self.ocupado = False
        self.impressoras = []
        self.atual = None  # (caminho, device_id, chave, perfil)
        self._estilos()
        self._montar()
        self.after(100, self._processar_fila)
        self.after(300, self.detectar)

    # ---------------- layout ----------------
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Azul.Horizontal.TProgressbar", troughcolor=CINZA_CLARO, background=AZUL,
                    bordercolor=CINZA_LINHA, lightcolor=AZUL, darkcolor=AZUL, thickness=14)
        s.configure("Alerta.Horizontal.TProgressbar", troughcolor=CINZA_CLARO, background=VERMELHO,
                    bordercolor=CINZA_LINHA, lightcolor=VERMELHO, darkcolor=VERMELHO, thickness=14)
        s.configure("TCombobox", fieldbackground=BRANCO, background=BRANCO)

    def _cartao(self, pai, titulo):
        f = tk.Frame(pai, bg=BRANCO, highlightbackground=CINZA_LINHA, highlightthickness=1)
        tk.Label(f, text=titulo.upper(), bg=BRANCO, fg=CINZA, font=(FONTE, 8, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        return f

    def _montar(self):
        topo = tk.Frame(self, bg=AZUL, height=78)
        topo.pack(fill="x")
        topo.pack_propagate(False)
        tk.Label(topo, text="Manutenção Epson", bg=AZUL, fg=BRANCO, font=(FONTE, 18, "bold")).pack(anchor="w", padx=22, pady=(14, 0))
        tk.Label(topo, text="Reset de almofadas  ·  Limpeza de cabeçote  ·  Verificação de jatos",
                 bg=AZUL, fg="#9FC3D3", font=(FONTE, 9)).pack(anchor="w", padx=22)
        tk.Frame(self, bg="#7E7B7B", height=3).pack(fill="x")

        corpo = tk.Frame(self, bg=CINZA_CLARO)
        corpo.pack(fill="both", expand=True, padx=18, pady=14)
        corpo.columnconfigure(0, weight=1, uniform="c")
        corpo.columnconfigure(1, weight=1, uniform="c")
        corpo.rowconfigure(1, weight=1)

        # --- impressora
        ci = self._cartao(corpo, "Impressora")
        ci.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        self.lbl_modelo = tk.Label(ci, text="Procurando…", bg=BRANCO, fg=AZUL, font=(FONTE, 16, "bold"))
        self.lbl_modelo.pack(anchor="w", padx=16)
        self.lbl_info = tk.Label(ci, text="", bg=BRANCO, fg=CINZA, font=(FONTE, 9), justify="left")
        self.lbl_info.pack(anchor="w", padx=16, pady=(2, 6))
        self.lbl_conf = tk.Label(ci, text="", bg=BRANCO, fg=BRANCO, font=(FONTE, 8, "bold"), padx=8, pady=2)
        self.lbl_conf.pack(anchor="w", padx=16)
        linha = tk.Frame(ci, bg=BRANCO)
        linha.pack(fill="x", padx=16, pady=(10, 8))
        self.cmb = ttk.Combobox(linha, state="readonly", width=26)
        self.cmb.pack(side="left", fill="x", expand=True)
        self.cmb.bind("<<ComboboxSelected>>", lambda e: self._selecionar(self.cmb.current()))
        self.b_detectar = Botao(linha, "⟳  Detectar", self.detectar, cor=CINZA, cor_hover=CINZA_ESCURO)
        self.b_detectar.config(pady=4, padx=10, font=(FONTE, 9, "bold"))
        self.b_detectar.pack(side="left", padx=(8, 0))
        linha_ip = tk.Frame(ci, bg=BRANCO)
        linha_ip.pack(fill="x", padx=16, pady=(0, 14))
        tk.Label(linha_ip, text="IP da impressora:", bg=BRANCO, fg=CINZA, font=(FONTE, 9)).pack(side="left")
        self.ent_ip = tk.Entry(linha_ip, width=16, relief="flat", bg=CINZA_CLARO, fg=CINZA_ESCURO,
                               font=(FONTE, 10), highlightthickness=1, highlightbackground=CINZA_LINHA)
        self.ent_ip.pack(side="left", padx=(6, 0), ipady=3, fill="x", expand=True)
        self.ent_ip.bind("<Return>", lambda e: self.conectar_ip())
        self.b_ip = Botao(linha_ip, "Conectar", self.conectar_ip, cor=AZUL, cor_hover=AZUL_CLARO)
        self.b_ip.config(pady=3, padx=10, font=(FONTE, 9, "bold"))
        self.b_ip.pack(side="left", padx=(8, 0))

        # --- contadores
        cc = self._cartao(corpo, "Contadores das almofadas")
        cc.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 12))
        self.area_cont = tk.Frame(cc, bg=BRANCO)
        self.area_cont.pack(fill="both", expand=True, padx=16, pady=(2, 14))
        tk.Label(self.area_cont, text="Clique em “Ler contadores”.", bg=BRANCO, fg=CINZA, font=(FONTE, 9)).pack(anchor="w")

        # --- ações
        ca = self._cartao(corpo, "Ações")
        ca.grid(row=1, column=0, columnspan=2, sticky="nsew")
        grade = tk.Frame(ca, bg=BRANCO)
        grade.pack(fill="x", padx=16, pady=(4, 10))
        for i in range(3):
            grade.columnconfigure(i, weight=1, uniform="b")
        self.b_ler = Botao(grade, "Ler contadores", self.ler, cor=CINZA, cor_hover=CINZA_ESCURO)
        self.b_reset = Botao(grade, "Resetar almofadas", self.reset)
        self.b_jatos = Botao(grade, "Verificar jatos", self.jatos, cor=AZUL_SUAVE, cor_hover="#C3DAE4", fg=AZUL)
        self.b_limpa = Botao(grade, "Limpeza de cabeçote", lambda: self.limpeza(False), cor=AZUL_SUAVE, cor_hover="#C3DAE4", fg=AZUL)
        self.b_intensa = Botao(grade, "Limpeza intensa", lambda: self.limpeza(True), cor=CINZA_ESCURO, cor_hover="#222")
        self.b_ler.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.b_reset.grid(row=0, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        self.b_jatos.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self.b_limpa.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.b_intensa.grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        self.botoes = [self.b_ler, self.b_reset, self.b_jatos, self.b_limpa, self.b_intensa]

        self.log = tk.Text(ca, height=7, bg="#F7F8F8", fg=CINZA_ESCURO, relief="flat", font=("Consolas", 9),
                           highlightthickness=1, highlightbackground=CINZA_LINHA, wrap="word")
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.log.tag_config("ok", foreground=VERDE)
        self.log.tag_config("erro", foreground=VERMELHO)
        self.log.tag_config("info", foreground=AZUL)

        rod = tk.Frame(self, bg=CINZA, height=26)
        rod.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(rod, text="Pronto", bg=CINZA, fg=BRANCO, font=(FONTE, 9))
        self.lbl_status.pack(side="left", padx=12)
        n = len(carregar_modelos())
        tk.Label(rod, text="%d perfis de modelo" % n, bg=CINZA, fg="#D9D9D9", font=(FONTE, 8)).pack(side="right", padx=12)
        self._atualizar_botoes()

    # ---------------- utilidades ----------------
    def escrever(self, txt, tag=None):
        self.log.insert("end", txt + "\n", tag)
        self.log.see("end")

    def _processar_fila(self):
        try:
            while True:
                fn, a = self.fila.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)

    def ui(self, fn, *a):
        self.fila.put((fn, a))

    def _atualizar_botoes(self):
        tem = self.atual is not None
        perfil = self.atual[3] if tem else None
        self.b_detectar.habilitar(not self.ocupado)
        self.b_ip.habilitar(not self.ocupado)
        self.b_ler.habilitar(not self.ocupado and bool(perfil))
        self.b_reset.habilitar(not self.ocupado and bool(perfil and perfil.get("reset")))
        for b in (self.b_jatos, self.b_limpa, self.b_intensa):
            b.habilitar(not self.ocupado and tem)

    def tarefa(self, nome, fn):
        if self.ocupado:
            return
        self.ocupado = True
        self.lbl_status.config(text=nome + "…")
        self._atualizar_botoes()

        def run():
            try:
                log.info("Inicio: %s (%s)", nome, self.atual[1].get("MDL") if self.atual else "-")
                fn()
            except Exception as e:
                log.exception("Falha em %s", nome)
                titulo, msg = amigavel(e, nome)
                self.ui(self.escrever, "✖ %s — %s" % (titulo, msg.split("\n")[0]), "erro")
                self.ui(self.lbl_status.config, {"text": titulo})
                self.ui(messagebox.showwarning, titulo, msg)
            else:
                self.ui(self.lbl_status.config, {"text": "Pronto"})
            finally:
                def fim():
                    self.ocupado = False
                    self._atualizar_botoes()
                self.ui(fim)

        threading.Thread(target=run, daemon=True).start()

    # ---------------- detecção ----------------
    def detectar(self):
        def fn():
            lst = []
            for p in list_usb_printers():
                if "vid_04b8" not in p.lower():
                    continue
                try:
                    with UsbPrinter(p) as u:
                        did = parse_device_id(u.device_id())
                except OSError:
                    log.exception("Nao foi possivel abrir %s", p)
                    continue
                if did.get("MDL"):
                    lst.append((p, did))
            self.ui(self.lbl_status.config, {"text": "Procurando na rede…"})
            try:
                for ip, texto in rede.descobrir(2.5, rede.ips_das_filas_windows()):
                    did = parse_device_id(texto)
                    if did.get("MDL"):
                        lst.append(("rede:" + ip, did))
            except Exception:
                log.exception("Falha na descoberta pela rede")
            self.ui(self._mostrar_lista, lst)
        self.tarefa("Detectando impressoras", fn)

    def conectar_ip(self):
        ip = self.ent_ip.get().strip()
        try:
            rede.ipaddress.IPv4Address(ip)
        except ValueError:
            messagebox.showwarning("IP inválido", "Digite o IP da impressora, por exemplo 192.168.0.50.\n\n"
                                   "O IP aparece na folha de status de rede da impressora.")
            return

        def fn():
            try:
                did = parse_device_id(rede.device_id(ip))
            except rede.SnmpSemObjeto:
                did = {}
            if not did.get("MDL") or "EPSON" not in did.get("MFG", "EPSON").upper():
                log.info("IP %s respondeu mas nao e Epson: %r", ip, did)
                self.ui(messagebox.showwarning, "Não é uma Epson",
                        "O aparelho no IP %s respondeu, mas não é uma impressora Epson." % ip)
                return
            novos = [x for x in self.impressoras if x[0] != "rede:" + ip] + [("rede:" + ip, did)]
            self.ui(self._mostrar_lista, novos, len(novos) - 1)
        self.tarefa("Conectando em " + ip, fn)

    @staticmethod
    def _rotulo(item):
        p, d = item
        via = "Wi-Fi/rede " + p[5:] if p.startswith("rede:") else "USB"
        return "%s  ·  %s" % (d.get("MDL", "?").replace(" Series", ""), via)

    def _mostrar_lista(self, lst, escolher=0):
        self.impressoras = lst
        if not lst:
            self.atual = None
            self.cmb["values"] = []
            self.cmb.set("")
            self.lbl_modelo.config(text="Nenhuma Epson encontrada")
            self.lbl_info.config(text="Conecte pelo cabo USB ou deixe a impressora na\nmesma rede Wi-Fi e clique em Detectar.")
            self.lbl_conf.config(text="", bg=BRANCO)
            self.escrever("Nenhuma Epson encontrada na USB nem na rede. Se souber o IP, digite e clique em Conectar.", "erro")
            self._atualizar_botoes()
            return
        self.cmb["values"] = [self._rotulo(x) for x in lst]
        self.cmb.current(escolher)
        self._selecionar(escolher)

    def _selecionar(self, i):
        p, did = self.impressoras[i]
        mdl = did.get("MDL", "")
        chave, perfil = achar_modelo(mdl)
        self.atual = (p, did, chave, perfil)
        nome_mdl = mdl.replace(" Series", "").strip()
        self.lbl_modelo.config(text="EPSON " + nome_mdl)
        log.info("Detectada %s em %s (perfil %s)", mdl, p, chave)
        porta = ("Conectada pela rede (%s)" % p[5:]) if p.startswith("rede:") else "Conectada pela USB"
        if perfil:
            familia = "Perfil próprio" if chave == nome_mdl.upper() else "Mesma família da %s" % chave
            self.lbl_info.config(text="%s\n%s" % (familia, porta))
            c = perfil.get("confianca", "C")
            self.lbl_conf.config(text="  " + CONFIANCA.get(c, c) + "  ", bg=COR_CONF.get(c, CINZA))
            if not perfil.get("reset"):
                self.escrever("Para este modelo o reset ainda não está disponível. "
                              "Leitura, limpeza e verificação de jatos funcionam normalmente.", "info")
        else:
            self.lbl_info.config(text="Modelo sem reset cadastrado\n%s" % porta)
            self.lbl_conf.config(text="  Só limpeza e teste de jatos  ", bg=CINZA)
        self.escrever("Impressora encontrada: EPSON %s (%s)" % (nome_mdl, porta.replace("Conectada ", "")), "info")
        self._limpar_contadores()
        self._atualizar_botoes()

    # ---------------- contadores ----------------
    def _limpar_contadores(self):
        for w in self.area_cont.winfo_children():
            w.destroy()
        tk.Label(self.area_cont, text="Clique em “Ler contadores”.", bg=BRANCO, fg=CINZA, font=(FONTE, 9)).pack(anchor="w")

    def _mostrar_contadores(self, cont):
        for w in self.area_cont.winfo_children():
            w.destroy()
        for nome, c in cont.items():
            linha = tk.Frame(self.area_cont, bg=BRANCO)
            linha.pack(fill="x", pady=4)
            pct = c["percentual"]
            txt = ("%.1f%%" % pct) if pct is not None else "valor %d" % c["valor"]
            tk.Label(linha, text=NOMES.get(nome, nome), bg=BRANCO, fg=CINZA_ESCURO, font=(FONTE, 9)).pack(anchor="w")
            barra_l = tk.Frame(linha, bg=BRANCO)
            barra_l.pack(fill="x")
            estilo = "Alerta.Horizontal.TProgressbar" if (pct or 0) >= 90 else "Azul.Horizontal.TProgressbar"
            pb = ttk.Progressbar(barra_l, style=estilo, maximum=100, value=min(pct or 0, 100))
            pb.pack(side="left", fill="x", expand=True)
            tk.Label(barra_l, text=txt, bg=BRANCO, fg=AZUL, width=9, anchor="e", font=(FONTE, 10, "bold")).pack(side="left")

    def _sessao(self, fn):
        p, did, chave, perfil = self.atual
        if p.startswith("rede:"):
            return fn(Epson(rede.RedeLink(p[5:]), perfil), did, chave)
        with UsbPrinter(p) as u, D4Link(u) as link:
            link.open(CTRL)
            return fn(Epson(link, perfil), did, chave)

    def ler(self):
        def fn():
            cont = self._sessao(lambda ep, did, chave: ep.contadores())
            self.ui(self._mostrar_contadores, cont)
            if self.atual[3].get("confianca") == "D":
                self.ui(self.lbl_conf.config, {"text": "  Leitura liberada (firmware não bloqueado)  ", "bg": LARANJA})
            for n, c in cont.items():
                pct = c["percentual"]
                self.ui(self.escrever, "%s: %s" % (NOMES.get(n, n), ("%.2f%%" % pct) if pct is not None else c["valor"]))
        self.tarefa("Lendo contadores", fn)

    def reset(self):
        mdl = self.atual[1].get("MDL")
        conf = self.atual[3].get("confianca")
        aviso = "\n\nAtenção: perfil de confiança %s (%s)." % (conf, CONFIANCA.get(conf)) if conf in ("C", "D") else ""
        if not messagebox.askyesno("Resetar almofadas",
                                   "Zerar os contadores das almofadas da %s?\n\nUm backup dos valores atuais será salvo "
                                   "antes. Troque ou limpe as almofadas físicas.%s" % (mdl, aviso)):
            return

        def fn():
            def corpo(ep, did, chave):
                antes = ep.contadores()
                nome = salvar_backup(chave, did, ep.backup())
                log.info("Backup salvo em %s", nome)
                self.ui(self.escrever, "Backup dos contadores salvo na pasta 'backups'.", "info")
                ep.resetar(lambda i, n: self.ui(self.lbl_status.config, {"text": "Gravando %d/%d…" % (i, n)}))
                return antes, ep.contadores()
            antes, depois = self._sessao(corpo)
            self.ui(self._mostrar_contadores, depois)
            self.ui(self.escrever, "✔ Reset concluído. Desligue e ligue a impressora.", "ok")
            self.ui(messagebox.showinfo, "Reset concluído", "Contadores zerados.\nDesligue e ligue a impressora.")
        self.tarefa("Resetando", fn)

    # ---------------- manutenção ----------------
    def _manut(self, titulo, dados):
        p, did, _, _ = self.atual

        def fn():
            if p.startswith("rede:"):
                rede.enviar_raw_9100(p[5:], dados)
                via = "rede 9100"
            else:
                via = enviar_manutencao(did.get("MDL", ""), p, dados)
            log.info("%s enviado por %s", titulo, via)
            if titulo.startswith("Verificação"):
                fim = "Aguarde a folha de teste sair da impressora."
            else:
                fim = "Aguarde a impressora terminar (1 a 2 minutos) antes de usar."
            self.ui(self.escrever, "✔ %s enviada. %s" % (titulo, fim), "ok")
        self.tarefa(titulo, fn)

    def jatos(self):
        self._manut("Verificação de jatos", cmd_teste_jatos())

    def limpeza(self, intensa):
        if intensa:
            if not messagebox.askyesno("Limpeza intensa",
                                       "A limpeza intensa gasta bastante tinta.\n"
                                       "Use só se a limpeza normal não resolveu.\n\nContinuar?"):
                return
            self._manut("Limpeza intensa", cmd_limpeza(True))
        else:
            self._manut("Limpeza de cabeçote", cmd_limpeza(False))


if __name__ == "__main__":
    App().mainloop()
