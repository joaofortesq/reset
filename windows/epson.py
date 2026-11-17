"""Comandos Epson: EEPROM (canal EPSON-CTRL) e remote mode (teste de jatos / limpezas)."""
import ctypes
import datetime
import json
import os
import re
import struct
import sys
from ctypes import wintypes as wt

from d4 import D4Link, CTRL

# Pasta dos recursos embutidos (PyInstaller) e pasta "de trabalho" (ao lado do .exe)
RECURSOS = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
AQUI = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))

CONFIANCA = {
    "A": "Validado (mesma família testada)",
    "B": "Duas fontes concordam",
    "C": "Uma fonte",
    "D": "Atenção: pode ser bloqueado",
}


def carregar_modelos():
    with open(os.path.join(RECURSOS, "modelos.json"), encoding="utf-8") as f:
        db = json.load(f)
    db.pop("_fonte", None)
    return db


def achar_modelo(mdl: str, db=None):
    """Acha o perfil pelo campo MDL do Device ID (ex.: 'L3150 Series')."""
    db = db or carregar_modelos()
    nome = mdl.upper().replace("SERIES", "").strip()
    if nome in db:
        return nome, db[nome]
    for chave, m in db.items():
        if nome in (a.upper() for a in m.get("aliases", [])):
            return chave, m
    return None, None


class LeituraRecusada(Exception):
    """A impressora não liberou a leitura da EEPROM (chave errada ou firmware bloqueado)."""


class GravacaoRecusada(Exception):
    """A gravação falhou; `gravados` diz quantos endereços já tinham sido gravados."""

    def __init__(self, msg, gravados=0):
        super().__init__(msg)
        self.gravados = gravados


class FalhaEnvio(Exception):
    """Não foi possível enviar o comando de manutenção (fila do Windows ou USB)."""


def ctrl_cmd(cmd: bytes, payload: bytes) -> bytes:
    return cmd + struct.pack("<H", len(payload)) + payload


class Epson:
    def __init__(self, link: D4Link, modelo: dict):
        self.link = link
        self.m = modelo

    def _ctrl(self, msg: bytes) -> bytes:
        return self.link.transact(CTRL, msg)

    # ---------- EEPROM ----------
    def ler(self, addr: int):
        rk = self.m["read_key"]
        pl = bytes([rk[0], rk[1], 0x41, 0xBE, 0xA0, addr & 0xFF, addr >> 8])
        r = self._ctrl(ctrl_cmd(b"||", pl))
        txt = r.decode("latin-1")
        mm = re.search(r"EE:([0-9A-Fa-f]{4})([0-9A-Fa-f]{2})", txt)
        if not mm:
            raise LeituraRecusada("Leitura recusada no endereco %d: %r" % (addr, txt))
        if int(mm.group(1), 16) != addr:
            raise LeituraRecusada("Endereco devolvido difere do pedido %d: %r" % (addr, txt))
        return int(mm.group(2), 16)

    def gravar(self, addr: int, valor: int):
        rk = self.m["read_key"]
        wk = bytes(0 if b == 0 else b + 1 for b in self.m["write_key"].encode("ascii"))
        pl = bytes([rk[0], rk[1], 0x42, 0xBD, 0x21, addr & 0xFF, addr >> 8, valor & 0xFF]) + wk
        r = self._ctrl(ctrl_cmd(b"||", pl))
        if b":OK;" not in r:
            raise GravacaoRecusada("Gravacao recusada no endereco %d: %r" % (addr, r))
        lido = self.ler(addr)  # alguns firmwares respondem OK sem gravar
        if lido != valor:
            raise GravacaoRecusada("Endereco %d: gravado %d mas a releitura deu %d" % (addr, valor, lido))

    def contadores(self):
        res = {}
        for nome, c in self.m["contadores"].items():
            bs = [self.ler(a) for a in c["enderecos"]]
            valor = 0
            for b in reversed(bs):
                valor = (valor << 8) | b
            pct = round(valor / c["divisor"], 2) if c.get("divisor") else None
            res[nome] = {"bytes": bs, "valor": valor, "percentual": pct}
        return res

    def pode_resetar(self):
        return bool(self.m.get("reset"))

    def backup(self):
        ends = set(int(a) for a in self.m["reset"]) | set(int(a) for a in self.m.get("commit", {}))
        return {a: self.ler(a) for a in sorted(ends)}

    def resetar(self, progresso=None):
        itens = list(self.m["reset"].items())
        for i, (a, v) in enumerate(itens):
            try:
                self.gravar(int(a), int(v))
            except GravacaoRecusada as e:
                e.gravados = i
                raise
            except Exception as e:
                raise GravacaoRecusada("Falha ao gravar endereco %s: %s" % (a, e), gravados=i) from e
            if progresso:
                progresso(i + 1, len(itens))
        for a, mask in self.m.get("commit", {}).items():  # byte de confirmação (plataformas novas)
            atual = self.ler(int(a))
            novo = atual & int(mask)
            if novo != atual:
                self.gravar(int(a), novo)


def salvar_backup(chave, device_id, dados):
    pasta = os.path.join(AQUI, "backups")
    os.makedirs(pasta, exist_ok=True)
    nome = os.path.join(pasta, "backup_%s_%s.json" % (chave, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    with open(nome, "w", encoding="utf-8") as f:
        json.dump({"modelo": chave, "device_id": device_id, "eeprom": dados}, f, indent=1)
    return nome


# ---------- Remote mode (mesma sequência do utilitário Epson) ----------
EXIT_PACKET_MODE = b"\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL     \n"
INIT = b"\x1b@"
REMOTE_ON = INIT + INIT + b"\x1b(R\x08\x00\x00REMOTE1"
REMOTE_OFF = b"\x1b\x00\x00\x00"


def _rc(cmd: str, args: bytes) -> bytes:
    return cmd.encode() + struct.pack("<H", len(args)) + args


def _ti():
    n = datetime.datetime.now()
    return _rc("TI", b"\x00" + n.year.to_bytes(2, "big") + bytes([n.month, n.day, n.hour, n.minute, n.second]))


JOB_END = _rc("JE", b"\x00")


def cmd_teste_jatos():
    # Cópia da sequência enviada pelo driver oficial (Preferências > Manutenção > Verificação dos jatos),
    # capturada da fila da L3250 em 13/11/2026. JS (início de trabalho) + FF (ejeta a folha) + JE (fim).
    return (EXIT_PACKET_MODE + REMOTE_ON + _ti() + _rc("JS", b"\x00\x00\x00\x00") + _rc("NC", b"\x00\x00")
            + REMOTE_OFF + b"\r\n\r\n"
            + b"\x1b(R\x08\x00\x00REMOTE1" + _rc("VI", b"\x00\x00") + _rc("LD", b"") + REMOTE_OFF
            + b"\x0c"
            + REMOTE_ON + JOB_END + REMOTE_OFF)


def cmd_limpeza(intensa=False):
    grupo = 0x00 | (0x10 if intensa else 0)  # 0 = todas as cores; bit 0x10 = power cleaning
    return (EXIT_PACKET_MODE + REMOTE_ON + _ti() + _rc("CH", bytes([0x00, grupo])) + REMOTE_OFF
            + REMOTE_ON + JOB_END + REMOTE_OFF)


class DOC_INFO_1(ctypes.Structure):
    _fields_ = [("pDocName", wt.LPWSTR), ("pOutputFile", wt.LPWSTR), ("pDatatype", wt.LPWSTR)]


def enviar_raw(fila: str, dados: bytes, nome="Maintenance"):  # mesmo nome usado pelo driver Epson
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    h = wt.HANDLE()
    if not ws.OpenPrinterW(fila, ctypes.byref(h), None):
        raise OSError(ctypes.get_last_error(), "OpenPrinter falhou: %s" % fila)
    try:
        di = DOC_INFO_1(nome, None, "RAW")
        if not ws.StartDocPrinterW(h, 1, ctypes.byref(di)):
            raise OSError(ctypes.get_last_error(), "StartDocPrinter falhou")
        ws.StartPagePrinter(h)
        n = wt.DWORD()
        ok = ws.WritePrinter(h, dados, len(dados), ctypes.byref(n))
        ws.EndPagePrinter(h)
        ws.EndDocPrinter(h)
        if not ok or n.value != len(dados):
            raise OSError(ctypes.get_last_error(), "WritePrinter falhou")
    finally:
        ws.ClosePrinter(h)


class PRINTER_INFO_2W(ctypes.Structure):
    _fields_ = [("pServerName", wt.LPWSTR), ("pPrinterName", wt.LPWSTR), ("pShareName", wt.LPWSTR),
                ("pPortName", wt.LPWSTR), ("pDriverName", wt.LPWSTR), ("pComment", wt.LPWSTR),
                ("pLocation", wt.LPWSTR), ("pDevMode", ctypes.c_void_p), ("pSepFile", wt.LPWSTR),
                ("pPrintProcessor", wt.LPWSTR), ("pDatatype", wt.LPWSTR), ("pParameters", wt.LPWSTR),
                ("pSecurityDescriptor", ctypes.c_void_p), ("Attributes", wt.DWORD), ("Priority", wt.DWORD),
                ("DefaultPriority", wt.DWORD), ("StartTime", wt.DWORD), ("UntilTime", wt.DWORD),
                ("Status", wt.DWORD), ("cJobs", wt.DWORD), ("AveragePPM", wt.DWORD)]


PRINTER_ENUM_LOCAL, PRINTER_ENUM_CONNECTIONS = 0x2, 0x4
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x400


def listar_filas():
    """[(nome, porta, offline)] via EnumPrintersW (API do Windows, sem PowerShell)."""
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    need, n = wt.DWORD(), wt.DWORD()
    ws.EnumPrintersW(flags, None, 2, None, 0, ctypes.byref(need), ctypes.byref(n))
    if not need.value:
        return []
    buf = (ctypes.c_byte * need.value)()
    if not ws.EnumPrintersW(flags, None, 2, buf, need, ctypes.byref(need), ctypes.byref(n)):
        return []
    itens = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_2W))
    return [(itens[i].pPrinterName or "", itens[i].pPortName or "",
             bool(itens[i].Attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE)) for i in range(n.value)]


def filas_do_modelo(mdl: str):
    """Filas do Windows (online) cujo nome contém o modelo."""
    nome = mdl.upper().replace("SERIES", "").strip()
    return [(n, p) for n, p, off in listar_filas() if nome and nome in n.upper() and not off]


def enviar_manutencao(mdl: str, caminho_usb: str, dados: bytes):
    """Envia pela fila do Windows; se não houver fila, escreve direto no USB."""
    filas = filas_do_modelo(mdl)
    try:
        if filas:
            enviar_raw(filas[0][0], dados)
            return "fila '%s'" % filas[0][0]
        from usb_win import UsbPrinter
        with UsbPrinter(caminho_usb, timeout_ms=5000) as u:
            if u.write(dados) != len(dados):
                raise OSError(0, "Escrita USB incompleta")
        return "USB direto"
    except OSError as e:
        raise FalhaEnvio("Envio falhou (%s): %s" % ("fila " + filas[0][0] if filas else "USB", e)) from e
