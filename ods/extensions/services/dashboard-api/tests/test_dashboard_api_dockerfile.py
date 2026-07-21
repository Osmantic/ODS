from pathlib import Path


def test_dashboard_api_image_includes_performance_evidence():
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"

    contents = dockerfile.read_text(encoding="utf-8")

    assert "COPY performance_evidence.json ./" in contents
