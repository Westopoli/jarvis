"""Preload the pip-installed cuBLAS/cuDNN shared libraries.

faster-whisper's ctranslate2 backend dlopens ``libcublas.so.12`` and
``libcudnn.so.9`` at inference time. Ollama ships private copies that are not
on the system loader path, so we depend on the ``nvidia-cublas-cu12`` and
``nvidia-cudnn-cu12`` wheels instead and load them with ``RTLD_GLOBAL``
before ctranslate2 asks for them. Setting ``LD_LIBRARY_PATH`` from Python is
too late (the dynamic loader reads it at process start), hence ``ctypes``.

Harmless when the wheels are absent: whisper then runs on CPU or fails with
its own error.
"""
from __future__ import annotations

import ctypes
import glob
import os
import site
import sys


def _candidate_dirs() -> list[str]:
    roots = [*site.getsitepackages(), site.getusersitepackages(), *sys.path]
    dirs = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        dirs.extend(glob.glob(os.path.join(root, "nvidia", "*", "lib")))
    return sorted(set(dirs))


def preload() -> list[str]:
    """Load every cuBLAS/cuDNN .so found in site-packages. Returns what loaded."""
    loaded: list[str] = []
    patterns = ("libcublasLt.so.*", "libcublas.so.*", "libcudnn.so.*")
    for directory in _candidate_dirs():
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(directory, pattern))):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                    loaded.append(path)
                except OSError:
                    continue
    return loaded
