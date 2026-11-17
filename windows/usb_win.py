"""Acesso bidirecional a impressoras USB no Windows via usbprint.sys (sem libusb).

Enumera GUID_DEVINTERFACE_USBPRINT, abre o dispositivo com CreateFile e faz
Read/Write com I/O sobreposto (timeout). Mesmo papel do apdadrv.dll do AdjProg.
"""
import ctypes
from ctypes import wintypes as wt

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    import uuid
    u = uuid.UUID(s)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


GUID_DEVINTERFACE_USBPRINT = _guid("28d78fad-5a12-11d1-ae5b-0000f803a8c2")
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
IOCTL_USBPRINT_GET_1284_ID = 0x220034


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]


setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                     ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ResetEvent.argtypes = [wt.HANDLE]


def list_usb_printers():
    """Retorna os caminhos de dispositivo de todas as impressoras USB presentes."""
    paths = []
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID_DEVINTERFACE_USBPRINT), None, None,
                                      DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if h == INVALID_HANDLE_VALUE:
        return paths
    try:
        i = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(GUID_DEVINTERFACE_USBPRINT), i,
                                                        ctypes.byref(did)):
                break
            need = wt.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            # cbSize de SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 em 64 bits, 6 em 32 bits
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(did), buf, need, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            i += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h)
    return paths


class UsbPrinter:
    def __init__(self, path, timeout_ms=2000):
        self.path = path
        self.timeout_ms = timeout_ms
        self.h = None

    def open(self):
        h = kernel32.CreateFileW(self.path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if h == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "Nao foi possivel abrir %s" % self.path)
        self.h = h
        self.ev = kernel32.CreateEventW(None, True, False, None)
        return self

    def close(self):
        if self.h:
            kernel32.CloseHandle(self.ev)
            kernel32.CloseHandle(self.h)
            self.h = None

    __enter__ = open

    def __exit__(self, *exc):
        self.close()

    def _io(self, fn, buf, n, timeout_ms):
        ov = OVERLAPPED()
        kernel32.ResetEvent(self.ev)
        ov.hEvent = self.ev
        done = wt.DWORD()
        ok = fn(self.h, buf, n, ctypes.byref(done), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(err, "Erro de I/O USB")
            if kernel32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(ov))
                kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(done), True)
                return done.value  # timeout: devolve o que chegou (normalmente 0)
            kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(done), True)
        return done.value

    def write(self, data: bytes) -> int:
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        return self._io(kernel32.WriteFile, buf, len(data), self.timeout_ms)

    def read(self, size=4096, timeout_ms=None) -> bytes:
        buf = ctypes.create_string_buffer(size)
        n = self._io(kernel32.ReadFile, buf, size, self.timeout_ms if timeout_ms is None else timeout_ms)
        return buf.raw[:n]

    def device_id(self) -> str:
        out = ctypes.create_string_buffer(1024)
        ov = OVERLAPPED()
        kernel32.ResetEvent(self.ev)
        ov.hEvent = self.ev
        done = wt.DWORD()
        ok = kernel32.DeviceIoControl(self.h, IOCTL_USBPRINT_GET_1284_ID, None, 0, out, 1024, ctypes.byref(done),
                                      ctypes.byref(ov))
        if not ok and ctypes.get_last_error() == ERROR_IO_PENDING:
            kernel32.WaitForSingleObject(self.ev, self.timeout_ms)
            kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(done), True)
        raw = out.raw[:done.value]
        return raw[2:].decode("latin-1", "replace") if len(raw) > 2 else ""


def parse_device_id(s: str) -> dict:
    d = {}
    for part in s.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k.strip()] = v.strip()
    return d
