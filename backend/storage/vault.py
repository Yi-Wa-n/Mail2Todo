"""Windows user-bound DPAPI storage. Never stores plaintext credentials."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]/'private'/'runtime'

def account_id():
    path = HOME/'active-account.json'
    value = json.loads(path.read_text(encoding='utf-8'))['id'] if path.exists() else 'legacy'
    import re
    if value != 'legacy' and not re.fullmatch(r'[0-9a-f]{64}', value):
        raise RuntimeError('邮箱台账配置无效')
    return value

def account_home(key=None):
    key = account_id() if key is None else key
    if key == 'legacy': return HOME
    import re
    if not re.fullmatch(r'[0-9a-f]{64}', key): raise ValueError('无效台账标识')
    return HOME/'accounts'/key


def physical_home():
    """Resolve MSIX LocalAppData virtualization for an unpackaged scheduled task."""
    import msvcrt
    with (HOME / 'ledger.sqlite3').open('rb') as file:
        library = ctypes.WinDLL('kernel32', use_last_error=True)
        library.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD]
        library.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = library.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(file.fileno()),buffer,len(buffer),0)
        if not size or size >= len(buffer):
            raise RuntimeError('无法确认现有本机数据的实际位置')
        path = buffer.value
        if path.startswith('\\\\?\\'):
            path = path[4:]
        return Path(path).parent


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('凭证存储仅支持当前 Windows 用户')
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    lib = ctypes.WinDLL('crypt32', use_last_error=True)
    if decrypt:
        ok = lib.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        ok = lib.CryptProtectData(ctypes.byref(src), 'AutumnRecruitMail', None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise RuntimeError('Windows 加密存储失败')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)


def save_credentials(value, home=None):
    home = account_home() if home is None else home
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'credentials.dpapi'
    temp = path.with_suffix('.new')
    temp.write_bytes(crypt(json.dumps(value, ensure_ascii=False).encode('utf-8')))
    os.replace(temp, path)


def load_credentials(home=None):
    path = (account_home() if home is None else home) / 'credentials.dpapi'
    if not path.exists():
        raise RuntimeError('本机版尚未配置网易邮箱授权')
    return json.loads(crypt(path.read_bytes(), decrypt=True))
