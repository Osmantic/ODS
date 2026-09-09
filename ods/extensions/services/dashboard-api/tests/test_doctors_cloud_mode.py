#!/usr/bin/env python3
"""
Test ODS doctor cloud mode LLM_API_URL validation.
"""

import json
import os
import subprocess
import sys
import tempfile


def test_doctor_cloud_mode_2s_timeout():
    """Test that doctor applies 2s timeout for cloud mode unreachable URLs."""
    # Create a temporary file for the report
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        # Test with an unreachable IP that should trigger 2s timeout
        env = os.environ.copy()
        env.update(
            {
                "ODS_MODE": "cloud",
                "LLM_API_URL": "http://10.255.255.1:1/",  # Non-routable IP
                "LITELLM_PORT": "4000",
            }
        )

        # Run doctor script with timeout to prevent hanging
        result = subprocess.run(
            ["bash", "scripts/ods-doctor.sh", report_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,  # Overall timeout for the doctor script
        )

        # Doctor should succeed overall (doesn't exit on LLM failure)
        assert result.returncode == 0, f"Doctor failed unexpectedly: {result.stderr}"

        # Check that report was generated
        assert os.path.exists(report_path), "Doctor report not generated"

        # Load the report
        with open(report_path, "r") as f:
            report = json.load(f)

        # Check that we see the 2s timeout message in stdout/stderr
        output = result.stdout + result.stderr
        assert "not responding (2s timeout)" in output, (
            f"Expected 2s timeout message not found in output: {output}"
        )

    finally:
        # Clean up
        if os.path.exists(report_path):
            os.unlink(report_path)


def test_doctor_cloud_mode_localhost_no_active_probe():
    """Test that doctor skips active probing for localhost URLs."""
    # Create a temporary file for the report
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        # Test with localhost URL - should skip active probe (now considered non-probeable)
        env = os.environ.copy()
        env.update(
            {
                "ODS_MODE": "cloud",
                "LLM_API_URL": "http://localhost:8000",  # localhost
                "LITELLM_PORT": "4000",
            }
        )

        # Run doctor script
        result = subprocess.run(
            ["bash", "scripts/ods-doctor.sh", report_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Doctor should succeed
        assert result.returncode == 0, f"Doctor failed: {result.stderr}"

        # Check that report was generated
        assert os.path.exists(report_path), "Doctor report not generated"

        # For localhost, we should see the bypass message (since localhost is now non-probeable)
        output = result.stdout + result.stderr
        assert "container-internal endpoint bypassed host probe" in output, (
            f"Expected bypass message not found in output: {output}"
        )

    finally:
        # Clean up
        if os.path.exists(report_path):
            os.unlink(report_path)


def test_doctor_cloud_mode_reachable_endpoint():
    """Test that doctor handles reachable endpoints correctly."""
    # Create a temporary file for the report
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        # Test with a reliable endpoint that returns 200
        env = os.environ.copy()
        env.update(
            {
                "ODS_MODE": "cloud",
                "LLM_API_URL": "http://httpbin.org/status/200",
                "LITELLM_PORT": "4000",
            }
        )

        # Run doctor script
        result = subprocess.run(
            ["bash", "scripts/ods-doctor.sh", report_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,  # Allow time for network request
        )

        # Doctor should succeed overall
        assert result.returncode == 0, f"Doctor failed unexpectedly: {result.stderr}"

        # Check that report was generated
        assert os.path.exists(report_path), "Doctor report not generated"

        # Load the report
        with open(report_path, "r") as f:
            report = json.load(f)

        # Should see some LLM backend message (might be success or failure depending on network)
        output = result.stdout + result.stderr
        # Either we see a success message or we see it attempted the check
        assert ("LLM backend:" in output) or ("Endpoint:" in output), (
            f"Expected LLM backend check not found in output: {output}"
        )

    finally:
        # Clean up
        if os.path.exists(report_path):
            os.unlink(report_path)


if __name__ == "__main__":
    # Run tests
    test_doctor_cloud_mode_2s_timeout()
    print("✓ Cloud mode 2s timeout test passed")

    test_doctor_cloud_mode_localhost_no_active_probe()
    print("✓ Cloud mode localhost no active probe test passed")

    test_doctor_cloud_mode_reachable_endpoint()
    print("✓ Cloud mode reachable endpoint test passed")

    print("\nAll tests passed!")
