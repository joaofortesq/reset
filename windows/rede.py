"""Acesso pela rede (Wi-Fi/cabo de rede) a impressoras Epson.

- SNMP v1 (UDP 161, comunidade "public"), implementado aqui sem bibliotecas externas.
- Os comandos EPSON-CTRL (ex.: leitura/gravação de EEPROM "||") viajam dentro do OID:
  1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1.<bytes do comando>  -> resposta em OCTET STRING.
- Descoberta: GET do Device ID Epson por broadcast em todas as redes locais.
- Teste de jatos / limpeza: os mesmos bytes, enviados pela porta RAW 9100.
"""
import ipaddress
import os
import socket
import struct
import time

OID_CTRL = "1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1"
OID_DEVICE_ID = "1.3.6.1.4.1.1248.1.2.2.1.1.1.1.1"
OID_SYSNAME = "1.3.6.1.2.1.1.5.0"
COMUNIDADE = b"public"


class SnmpErro(Exception):
    pass


class SnmpSemObjeto(SnmpErro):
    """O aparelho respondeu, mas não conhece o OID pedido (ex.: não é uma Epson)."""


# ---------------- BER mínimo ----------------
def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, val):
    return bytes([tag]) + _len(len(val)) + val


def _int(v):
    return _tlv(0x02, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True))


def _oid(s):
    p = [int(x) for x in s.split(".")]
    out = bytearray([40 * p[0] + p[1]])
    for n in p[2:]:
        enc = [n & 0x7F]
        n >>= 7
        while n:
            enc.insert(0, 0x80 | (n & 0x7F))
            n >>= 7
        out += bytes(enc)
    return _tlv(0x06, bytes(out))


def _ler_tlv(b, i):
    tag = b[i]
    n = b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, b[i:i + n], i + n


def montar_get(oid, req_id):
    vb = _tlv(0x30, _tlv(0x30, _oid(oid) + b"\x05\x00"))
    pdu = _tlv(0xA0, _int(req_id) + _int(0) + _int(0) + vb)
    return _tlv(0x30, _int(0) + _tlv(0x04, COMUNIDADE) + pdu)


def ler_resposta(b):
    """Devolve (req_id, erro, valor_bytes_ou_None)."""
    _, msg, _ = _ler_tlv(b, 0)
    _, _, i = _ler_tlv(msg, 0)          # versão
    _, _, i = _ler_tlv(msg, i)          # comunidade
    tag, pdu, _ = _ler_tlv(msg, i)
    if tag != 0xA2:
        raise SnmpErro("Resposta SNMP inesperada")
    _, rid, j = _ler_tlv(pdu, 0)
    _, err, j = _ler_tlv(pdu, j)
    _, _, j = _ler_tlv(pdu, j)
    _, vbl, _ = _ler_tlv(pdu, j)
    _, vb, _ = _ler_tlv(vbl, 0)
    _, _, k = _ler_tlv(vb, 0)           # oid
    vtag, val, _ = _ler_tlv(vb, k)
    erro = int.from_bytes(err, "big")
    if vtag in (0x80, 0x81, 0x82, 0x05):  # noSuchObject / noSuchInstance / endOfMib / NULL
        val = None
    return int.from_bytes(rid, "big", signed=True), erro, val


def snmp_get(ip, oid, timeout=2.0, tentativas=2):
    rid = int.from_bytes(os.urandom(3), "big")
    pkt = montar_get(oid, rid)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(tentativas):
            s.sendto(pkt, (ip, 161))
            fim = time.time() + timeout
            while time.time() < fim:
                try:
                    data, _ = s.recvfrom(65535)
                except socket.timeout:
                    break
                try:
                    r, erro, val = ler_resposta(data)
                except Exception:
                    continue
                if r == rid:
                    if erro == 2:
                        raise SnmpSemObjeto("OID desconhecido em %s: %s" % (ip, oid))
                    if erro:
                        raise SnmpErro("A impressora recusou a consulta SNMP (erro %d)" % erro)
                    return val
    raise SnmpErro("Sem resposta SNMP de %s" % ip)


# ---------------- descoberta ----------------
def _broadcasts():
    """Endereços de broadcast das redes IPv4 locais (/24 quando a máscara não é conhecida)."""
    alvos = {"255.255.255.255"}
    try:
        import ctypes
        from ctypes import wintypes as wt

        class IP_ADDR_STRING(ctypes.Structure):
            pass
        IP_ADDR_STRING._fields_ = [("Next", ctypes.POINTER(IP_ADDR_STRING)), ("IpAddress", ctypes.c_char * 16),
                                   ("IpMask", ctypes.c_char * 16), ("Context", wt.DWORD)]

        class IP_ADAPTER_INFO(ctypes.Structure):
            pass
        IP_ADAPTER_INFO._fields_ = [
            ("Next", ctypes.POINTER(IP_ADAPTER_INFO)), ("ComboIndex", wt.DWORD),
            ("AdapterName", ctypes.c_char * 260), ("Description", ctypes.c_char * 132),
            ("AddressLength", wt.UINT), ("Address", ctypes.c_ubyte * 8), ("Index", wt.DWORD),
            ("Type", wt.UINT), ("DhcpEnabled", wt.UINT), ("CurrentIpAddress", ctypes.c_void_p),
            ("IpAddressList", IP_ADDR_STRING), ("GatewayList", IP_ADDR_STRING), ("DhcpServer", IP_ADDR_STRING),
            ("HaveWins", wt.BOOL), ("PrimaryWinsServer", IP_ADDR_STRING), ("SecondaryWinsServer", IP_ADDR_STRING),
            ("LeaseObtained", ctypes.c_ulong), ("LeaseExpires", ctypes.c_ulong)]
        iph = ctypes.WinDLL("iphlpapi")
        tam = wt.ULONG(0)
        iph.GetAdaptersInfo(None, ctypes.byref(tam))
        buf = ctypes.create_string_buffer(tam.value)
        if iph.GetAdaptersInfo(buf, ctypes.byref(tam)) == 0:
            a = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
            while a:
                ipn = a.contents.IpAddressList
                ip, mask = ipn.IpAddress.decode(), ipn.IpMask.decode()
                if ip and ip != "0.0.0.0":
                    rede = ipaddress.IPv4Network("%s/%s" % (ip, mask), strict=False)
                    alvos.add(str(rede.broadcast_address))
                a = a.contents.Next
    except Exception:
        pass
    return sorted(alvos)


def descobrir(tempo=2.5, extras=()):
    """Procura impressoras Epson na rede. Devolve [(ip, device_id_texto)]."""
    rid = int.from_bytes(os.urandom(3), "big")
    pkt = montar_get(OID_DEVICE_ID, rid)
    achadas = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        for alvo in list(_broadcasts()) + list(extras):
            try:
                s.sendto(pkt, (alvo, 161))
            except OSError:
                pass
        fim = time.time() + tempo
        while time.time() < fim:
            try:
                data, (ip, _) = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                r, erro, val = ler_resposta(data)
            except Exception:
                continue
            if r == rid and not erro and val and b"EPSON" in val.upper():
                achadas[ip] = val.decode("latin-1", "replace")
    return sorted(achadas.items(), key=lambda x: tuple(int(p) for p in x[0].split(".")))


def ips_das_filas_windows():
    """IPs de impressoras Epson já instaladas no Windows por porta de rede (para achar além do broadcast)."""
    import re
    try:
        from epson import listar_filas
        ips = set()
        for nome, porta, _ in listar_filas():
            if "EPSON" in nome.upper():
                m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", porta)
                if m:
                    ips.add(m.group(1))
        return sorted(ips)
    except Exception:
        return []


# ---------------- "link" compatível com a classe Epson ----------------
class RedeLink:
    """Mesmo papel do D4Link: transact(canal, comando) -> resposta, só que via SNMP."""

    def __init__(self, ip, timeout=3.0):
        self.ip = ip
        self.timeout = timeout

    def transact(self, sid, payload: bytes) -> bytes:
        oid = OID_CTRL + "." + ".".join(str(b) for b in payload)
        val = snmp_get(self.ip, oid, timeout=self.timeout)
        if val is None:
            raise SnmpErro("Comando não suportado pela impressora na rede")
        return val

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def device_id(ip):
    v = snmp_get(ip, OID_DEVICE_ID)
    return v.decode("latin-1", "replace") if v else ""


def enviar_raw_9100(ip, dados: bytes, timeout=10.0):
    with socket.create_connection((ip, 9100), timeout=timeout) as s:
        s.sendall(dados)
        s.shutdown(socket.SHUT_WR)
        time.sleep(0.5)
