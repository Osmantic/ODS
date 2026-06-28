#!/usr/bin/env python3
"""
M7 Dashboard Load Test — Swarm Validation
Tests agent monitoring dashboard under concurrent load.
Uses only standard library (no external dependencies).

Usage:
    python3 dashboard-load-test.py --agents 5 --duration 60
    python3 dashboard-load-test.py --agents 10 --ramp 5
"""

import argparse
import threading
import urllib.request
import urllib.error
import time
from datetime import datetime
import queue

DASHBOARD_API = "http://localhost:3002"
ENDPOINTS = [
    "/api/agents/metrics",
    "/api/agents/cluster",
    "/api/agents/tokens",
    "/api/agents/throughput"
]


class LoadTester:
    def __init__(self, num_agents: int, duration: int, ramp: int = 0):
        self.num_agents = num_agents
        self.duration = duration
        self.ramp = ramp
        self.results: queue.Queue = queue.Queue()
        self.errors: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

    def agent_worker(self, agent_id: int):
        """Single agent making requests"""
        start_time = time.time()

        while not self.stop_event.is_set() and (time.time() - start_time) < self.duration:
            for endpoint in ENDPOINTS:
                url = f"{DASHBOARD_API}{endpoint}"
                req_start = time.time()

                try:
                    req = urllib.request.Request(url, method='GET')
                    req.add_header('Accept', 'application/json')

                    with urllib.request.urlopen(req, timeout=5) as resp:
                        elapsed = (time.time() - req_start) * 1000

                        self.results.put({
                            "agent_id": agent_id,
                            "endpoint": endpoint,
                            "status": resp.status,
                            "time_ms": elapsed,
                            "timestamp": datetime.now().isoformat()
                        })

                except Exception as e:
                    self.errors.put({
                        "agent_id": agent_id,
                        "endpoint": endpoint,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    })

                time.sleep(0.1)  # Small delay between requests

    def run(self):
        """Execute load test with all agents"""
        print(f"🚀 Starting load test: {self.num_agents} agents × {self.duration}s")
        print(f"   Ramp: {self.ramp}s between agents")
        print(f"   Target: {DASHBOARD_API}")
        print()

        # Test connectivity first
        try:
            req = urllib.request.Request(f"{DASHBOARD_API}/health", method='GET')
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    print("✅ Dashboard API reachable")
                else:
                    print(f"⚠️  Dashboard API returned {resp.status}")
        except Exception as e:
            print(f"❌ Cannot reach Dashboard API: {e}")
            return

        # Spawn agents with ramp
        threads = []
        for i in range(self.num_agents):
            t = threading.Thread(target=self.agent_worker, args=(i,))
            threads.append(t)
            t.start()

            if self.ramp > 0 and i < self.num_agents - 1:
                time.sleep(self.ramp)
                print(f"   Spawned agent {i+1}/{self.num_agents}")

        print(f"   All {self.num_agents} agents running...")

        # Wait for duration
        time.sleep(self.duration)
        self.stop_event.set()

        # Wait for threads to finish
        for t in threads:
            t.join(timeout=5)

        # Analysis
        self.print_report()

    def print_report(self):
        """Print test results"""
        # Collect results
        results_list = []
        while not self.results.empty():
            try:
                results_list.append(self.results.get_nowait())
            except queue.Empty:
                break

        errors_list = []
        while not self.errors.empty():
            try:
                errors_list.append(self.errors.get_nowait())
            except queue.Empty:
                break

        total_requests = len(results_list) + len(errors_list)

        print("\n" + "="*60)
        print("LOAD TEST RESULTS")
        print("="*60)

        if not results_list:
            print("❌ No successful requests")
            return

        # Calculate statistics
        times = [r["time_ms"] for r in results_list]
        avg_time = sum(times) / len(times)
        max_time = max(times)
        min_time = min(times)

        status_codes = {}
        for r in results_list:
            code = r["status"]
            status_codes[code] = status_codes.get(code, 0) + 1

        print("\n📊 Summary:")
        print(f"   Total requests: {total_requests}")
        print(f"   Successful: {len(results_list)}")
        print(f"   Failed: {len(errors_list)}")
        success_rate = len(results_list)/(total_requests)*100 if total_requests > 0 else 0
        print(f"   Success rate: {success_rate:.1f}%")

        print("\n⏱️  Latency:")
        print(f"   Min: {min_time:.1f}ms")
        print(f"   Avg: {avg_time:.1f}ms")
        print(f"   Max: {max_time:.1f}ms")

        print("\n📡 Status Codes:")
        for code, count in sorted(status_codes.items()):
            print(f"   {code}: {count}")

        if errors_list:
            print(f"\n❌ Errors ({len(errors_list)}):")
            for e in errors_list[:5]:
                print(f"   Agent {e['agent_id']}: {e['endpoint']} - {e['error'][:60]}")
            if len(errors_list) > 5:
                print(f"   ... and {len(errors_list)-5} more")

        # Per-endpoint breakdown
        print("\n🔍 Per-Endpoint Average:")
        for endpoint in ENDPOINTS:
            endpoint_times = [r["time_ms"] for r in results_list if r["endpoint"] == endpoint]
            if endpoint_times:
                avg = sum(endpoint_times) / len(endpoint_times)
                print(f"   {endpoint}: {avg:.1f}ms ({len(endpoint_times)} reqs)")

        print("\n" + "="*60)

        # Pass/Fail criteria
        if success_rate >= 95 and avg_time < 500:
            print("✅ PASS — Dashboard handles load well")
        elif success_rate >= 90:
            print("⚠️  MARGINAL — Some degradation under load")
        else:
            print("❌ FAIL — Significant issues under load")


def main():
    parser = argparse.ArgumentParser(description="Dashboard API Load Test")
    parser.add_argument("--agents", "-a", type=int, default=5, help="Number of concurrent agents")
    parser.add_argument("--duration", "-d", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--ramp", "-r", type=int, default=0, help="Seconds between agent spawns")
    args = parser.parse_args()

    tester = LoadTester(args.agents, args.duration, args.ramp)
    tester.run()


if __name__ == "__main__":
    main()
