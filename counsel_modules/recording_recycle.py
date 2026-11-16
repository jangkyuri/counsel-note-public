from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Final
from uuid import UUID

RECYCLE_ONLY_FLAGS: Final = 0x00080000 | 0x00100000 | 0x0400 | 0x0010 | 0x0004 | 0x2000 | 0x1000


def _guid(value: str) -> ctypes.Array[ctypes.c_char]:
    return ctypes.create_string_buffer(UUID(value).bytes_le, 16)


def _check(result: int, stage: str) -> None:
    if result < 0:
        raise OSError(result & 0xFFFFFFFF, f'Windows recycle-only operation failed: {stage}')


def _call0(pointer: ctypes.c_void_p, index: int) -> int:
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[index])(pointer)


def recycle_recording(value: str) -> None:
    """Require Windows recycle-only semantics; never fall back to permanent deletion."""
    if os.name != 'nt':
        raise OSError('Windows recycle-only API is required')
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise OSError('Recycle target must be an existing absolute regular file')
    ole = ctypes.WinDLL('ole32')
    shell = ctypes.WinDLL('shell32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long
    _check(ole.CoInitializeEx(None, 2), 'initialize')
    operation, item = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _check(ole.CoCreateInstance(_guid('3ad05575-8857-4850-9277-11b85bdb8e09'), None, 1,
                                   _guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8'), ctypes.byref(operation)), 'create')
        # IFileOperation vtable slots follow ShObjIdl_core.h, including IUnknown slots 0-2.
        table = ctypes.cast(operation, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_flags = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32)(table[5])
        _check(set_flags(operation, RECYCLE_ONLY_FLAGS), 'flags')
        _check(shell.SHCreateItemFromParsingName(str(path), None, _guid('43826d1e-e718-42ee-bc55-a1e261c37bfe'),
                                                ctypes.byref(item)), 'item')
        delete_item = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(table[18])
        _check(delete_item(operation, item, None), 'queue')
        _check(_call0(operation, 21), 'perform')
        aborted = ctypes.c_int()
        get_aborted = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(table[22])
        _check(get_aborted(operation, ctypes.byref(aborted)), 'completion')
        if aborted.value or path.exists():
            raise OSError('Windows did not confirm the recycle-only operation')
    finally:
        if item.value:
            _call0(item, 2)
        if operation.value:
            _call0(operation, 2)
        ole.CoUninitialize()
