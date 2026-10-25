"""Load an app-local Windows VC++ runtime without changing system DLLs."""
import os
from pathlib import Path
import sys

_handles = []


def prepare_runtime():
    if sys.platform != 'win32' or _handles:
        return
    import ctypes
    directory = Path(sys.prefix)/'Scripts'
    names = ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll',
             'msvcp140_1.dll', 'msvcp140_2.dll')
    # msvc-runtime installs these inside the venv. Explicit loading avoids
    # old System32 C++ DLLs shadowing them on Windows 11. No downloads here.
    if all((directory/name).is_file() for name in names):
        _handles.append(os.add_dll_directory(str(directory)))
        _handles.extend(ctypes.WinDLL(str(directory/name)) for name in names)
