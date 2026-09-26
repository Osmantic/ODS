"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestNormalizeGpuBackend:
    def test_nvidia_lowercase(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend("NVIDIA") == "nvidia"

    def test_amd(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend("amd") == "amd"

    def test_mps(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend("mps") == "mps"

    def test_unknown_returns_cpu(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend("cuda") == "cpu"

    def test_none_returns_cpu(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend(None) == "cpu"

    def test_empty_returns_cpu(self):
        from config import normalize_gpu_backend
        assert normalize_gpu_backend("") == "cpu"
