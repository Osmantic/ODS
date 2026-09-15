#!/usr/bin/env python3
from pathlib import Path

def test_tilde_expansion():
    path = Path("~/test_models")
    expanded = path.expanduser()
    assert str(expanded) != str(path)
    assert Path.home() in expanded.parents or expanded == Path.home()
    print("test_download_hf_snapshot_path_expansion passed.")

if __name__ == "__main__":
    test_tilde_expansion()
