"""Run with python tests/benchmark_scrub.py from the service directory."""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pii_scrubber import PrivacyShield


def main():
    results = []
    for count in (2000, 4000, 8000):
        payload = "\\n".join(f"contact-{index}@example.test" for index in range(count))
        samples = []
        for _ in range(3):
            shield = PrivacyShield()
            started = time.perf_counter()
            scrubbed, metadata = shield.process_request(payload)
            samples.append(time.perf_counter() - started)
            assert metadata["pii_count"] == count
            assert "@example.test" not in scrubbed
        results.append({
            "contacts": count,
            "input_bytes": len(payload.encode()),
            "seconds": samples,
            "median_seconds": statistics.median(samples),
        })
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
