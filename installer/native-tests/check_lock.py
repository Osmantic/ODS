"""Keep the small test harness on the native application's locked dependencies."""

from pathlib import Path
import tomllib


def registry_packages(path: Path) -> set[tuple[str, str]]:
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        (package["name"], package["version"])
        for package in lock["package"]
        if package.get("source")
    }


def main() -> None:
    root = Path(__file__).resolve().parent
    production = registry_packages(root.parent / "src-tauri" / "Cargo.lock")
    tests = registry_packages(root / "Cargo.lock")
    mismatches = sorted(tests - production)
    if mismatches:
        raise SystemExit(f"Native test dependencies differ from src-tauri/Cargo.lock: {mismatches}")
    print("Native test dependency versions match src-tauri/Cargo.lock.")


if __name__ == "__main__":
    main()
