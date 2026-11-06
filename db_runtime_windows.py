"""Windows runtime identity and directory trust, using the current process token."""
import ctypes
from ctypes import wintypes
from functools import lru_cache

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
pointer = ctypes.c_void_p
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.LocalFree.argtypes = [pointer]
advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(ctypes.c_wchar_p)]
advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
userenv.GetUserProfileDirectoryW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def sid_text(sid):
    text = ctypes.c_wchar_p()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel.LocalFree(ctypes.cast(text, pointer))


@lru_cache(maxsize=1)
def identity():
    """Ignore USERPROFILE/HOME inherited from another account after elevation."""
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise RuntimeError("Cannot identify the database runtime account.")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        account_sid = sid_text(pointer.from_buffer(buffer))
        length = wintypes.DWORD()
        userenv.GetUserProfileDirectoryW(token, None, ctypes.byref(length))
        if not length.value:
            raise RuntimeError("The executing account has no Windows profile.")
        profile = ctypes.create_unicode_buffer(length.value)
        if not userenv.GetUserProfileDirectoryW(token, profile, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        return profile.value, account_sid
    finally:
        kernel.CloseHandle(token)


def trusted_directory(path):
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    result = advapi.GetNamedSecurityInfoW(str(path), 1, 0x00000005, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if result:
        raise ctypes.WinError(result)
    try:
        allowed = {identity()[1], "S-1-5-18", "S-1-5-32-544"}
        if not owner.value or sid_text(owner) not in allowed or not dacl.value:
            raise RuntimeError("Database runtime directory has an unsafe Windows owner or access list.")
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        for index in range(count):
            ace = pointer()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            ace_flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            if ace_flags & 0x08 or ace_type in (1, 6, 10, 12):
                continue  # inherit-only and deny entries cannot grant current-directory write.
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            if not mask & 0x500D0156:
                continue
            if ace_type != 0 or sid_text(pointer(ace.value + 8)) not in allowed:
                raise RuntimeError("Database runtime directory can be written by another Windows account.")
    finally:
        kernel.LocalFree(descriptor)
