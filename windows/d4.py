"""IEEE 1284.4 ("D4") minimo, reproduzindo o que o apdadrv.dll do AdjProg envia.

Pacote: PSID SSID LEN(2, big-endian, inclui cabecalho) CREDIT CONTROL + payload.
Canal 0/0 = transacao; 2/2 = EPSON-CTRL (comandos de servico); 0x40/0x40 = EPSON-DATA.
"""
import struct
import time

ENTER_D4 = b"\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n"
CTRL = 0x02
DATA = 0x40


class D4Error(Exception):
    """etapa: 'entrar' (impressora não entrou no modo de manutenção) ou 'comunicacao'."""

    def __init__(self, msg, etapa="comunicacao"):
        super().__init__(msg)
        self.etapa = etapa


class D4Link:
    def __init__(self, port, verbose=False):
        self.port = port
        self.verbose = verbose
        self.rev = 0x10
        self.buf = b""
        self.credit = {}
        self.open_channels = set()

    def _log(self, *a):
        if self.verbose:
            print("[d4]", *a)

    # ---------- baixo nivel ----------
    def _send_packet(self, sid, payload, credit=1):
        pkt = struct.pack(">BBHBB", sid, sid, 6 + len(payload), credit, 0) + payload
        self._log(">>", pkt.hex(" "))
        self.port.write(pkt)

    def _recv_packet(self, tries=8):
        for _ in range(tries):
            if len(self.buf) >= 6:
                ln = struct.unpack(">H", self.buf[2:4])[0]
                if ln >= 6 and len(self.buf) >= ln:
                    pkt, self.buf = self.buf[:ln], self.buf[ln:]
                    self._log("<<", pkt.hex(" "))
                    sid, credit = pkt[0], pkt[4]
                    if credit:
                        self.credit[sid] = self.credit.get(sid, 0) + credit
                    return sid, pkt[6:]
                if ln < 6:  # lixo: descarta
                    self.buf = b""
            chunk = self.port.read(4096, timeout_ms=1500)
            if chunk:
                self.buf += chunk
        return None, None

    def _txn(self, payload, expect):
        """Envia comando no canal de transacao e espera a resposta (expect = cmd|0x80)."""
        self._send_packet(0, payload)
        for _ in range(6):
            sid, p = self._recv_packet()
            if sid is None:
                break
            if sid == 0 and p and p[0] == expect:
                return p
            if sid == 0 and p and p[0] == 0x7F:
                raise D4Error("Erro D4 da impressora: %s" % p.hex(" "))
        raise D4Error("Sem resposta ao comando D4 0x%02x" % (expect & 0x7F))

    # ---------- sessao ----------
    def enter(self):
        self.port.write(ENTER_D4)
        time.sleep(0.2)
        self.buf = b""
        r = self.port.read(4096, timeout_ms=1500)  # resposta tipica: 00 00 00 08 01 00 c5 00
        self._log("enter reply", r.hex(" "))
        for rev in (0x10, 0x20):
            self.buf = b""
            try:
                p = self._txn(bytes([0x00, rev]), 0x80)  # Init
            except D4Error:
                continue
            if p[1] == 0x00:
                self.rev = rev
                return True
            if p[1] == 0x02 and len(p) > 2:  # revisao diferente: tenta a sugerida
                continue
        raise D4Error("Impressora nao aceitou Init D4 (nao entrou em IEEE 1284.4)", etapa="entrar")

    def open(self, sid, size=0x0200):
        if self.rev == 0x10:
            pl = struct.pack(">BBBHHHH", 0x01, sid, sid, size, size, 0, 0)
        else:
            pl = struct.pack(">BBBHHH", 0x01, sid, sid, size, size, 0)
        p = self._txn(pl, 0x81)
        if p[1] != 0:
            raise D4Error("OpenChannel %#x recusado (resultado %#x)" % (sid, p[1]))
        self.open_channels.add(sid)
        self.credit.setdefault(sid, 0)

    def request_credit(self, sid):
        if self.rev == 0x10:
            pl = struct.pack(">BBBHH", 0x04, sid, sid, 0xFFFF, 0xFFFF)
        else:
            pl = struct.pack(">BBBH", 0x04, sid, sid, 0x0000)
        p = self._txn(pl, 0x84)
        if p[1] == 0 and len(p) >= 6:
            self.credit[sid] = self.credit.get(sid, 0) + struct.unpack(">H", p[4:6])[0]

    def transact(self, sid, payload):
        """Envia payload num canal e devolve a resposta (bytes)."""
        if self.credit.get(sid, 0) < 1:
            self.request_credit(sid)
        if self.credit.get(sid, 0) < 1:
            raise D4Error("Sem credito para enviar no canal %#x" % sid)
        self.credit[sid] -= 1
        self._send_packet(sid, payload, credit=1)
        for _ in range(6):
            rsid, p = self._recv_packet()
            if rsid is None:
                break
            if rsid == sid and p:
                return p
        raise D4Error("Sem resposta no canal %#x" % sid)

    def close(self):
        for sid in list(self.open_channels):
            try:
                pl = bytes([0x02, sid, sid, 0x00]) if self.rev == 0x10 else bytes([0x02, sid, sid])
                self._txn(pl, 0x82)
            except D4Error:
                pass
            self.open_channels.discard(sid)
        try:
            self._txn(b"\x08", 0x88)  # Exit: volta ao modo normal
        except D4Error:
            pass

    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, *exc):
        self.close()
