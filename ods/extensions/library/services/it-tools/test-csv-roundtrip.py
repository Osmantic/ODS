#!/usr/bin/env python3
"""Exercise the actual patched TypeScript with Node and parse it independently."""
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

CASES = [
    ("reported quotes", [{"item": "Tea, green", "quantity": 2}, {"item": 'Oat "bar"', "quantity": 3}],
     [["item", "quantity"], ["Tea, green", "2"], ['Oat "bar"', "3"]]),
    ("header punctuation", [{"a,b": "x", 'c"d': "y", "e\r\nf": "z", "": "w"}],
     [["a,b", 'c"d', "e\r\nf", ""], ["x", "y", "z", "w"]]),
    ("value controls", [{"lf": "one\ntwo", "cr": "one\rtwo", "crlf": "one\r\ntwo", "slash": r"one\ntwo", "unicode": "café 中文"}],
     [["lf", "cr", "crlf", "slash", "unicode"], ["one\ntwo", "one\rtwo", "one\r\ntwo", r"one\ntwo", "café 中文"]]),
    ("empty input", [], []),
    ("blank header and values", [{"": ""}, {"": ""}], [[""], [""], [""]]),
    ("missing and null", [{"a": 1, "b": None}, {"b": 2}], [["a", "b"], ["1", "null"], ["", "2"]]),
    ("missing single column", [{"a": "x"}, {}], [["a"], ["x"], [""]]),
    ("union order", [{"b": 1, "a": 2}, {"c": 3}], [["b", "a", "c"], ["1", "2", ""], ["", "", "3"]]),
    ("spaces and scalars", [{"a": "  text  ", "b": False, "c": 0}], [["a", "b", "c"], ["  text  ", "false", "0"]]),
    ("repeated quoting", [{"a": 'x,"y"', "b": '""'}], [["a", "b"], ['x,"y"', '""']]),
]

def main():
    source = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        shutil.copyfile(source, directory / "serializer.mts")
        (directory / "run.mjs").write_text(
            "import { convertArrayToCsv } from './serializer.mts';\n"
            "import { readFileSync } from 'node:fs';\n"
            "const arrays = JSON.parse(readFileSync(0, 'utf8'));\n"
            "process.stdout.write(JSON.stringify(arrays.map(array => convertArrayToCsv({array}))));\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["node", "--experimental-strip-types", str(directory / "run.mjs")],
            input=json.dumps([array for _, array, _ in CASES]),
            text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
    outputs = json.loads(result.stdout)
    assert len(outputs) == len(CASES)
    for (name, _, expected), output in zip(CASES, outputs):
        actual = list(csv.reader(io.StringIO(output, newline=""), strict=True))
        assert actual == expected, f"{name}: {actual!r} != {expected!r}; output={output!r}"
    print(f"PASS: {len(CASES)} independent CSV roundtrip cases")

if __name__ == "__main__":
    main()
