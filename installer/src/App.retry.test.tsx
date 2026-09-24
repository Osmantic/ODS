// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { checkSystem, checkPrerequisites, detectGpu, startInstall } from "./hooks/useTauri";

vi.mock("./hooks/useTauri", () => ({
  checkSystem: vi.fn(), checkPrerequisites: vi.fn(), detectGpu: vi.fn(),
  startInstall: vi.fn(), getInstallProgress: vi.fn(), openODSserver: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(checkSystem).mockResolvedValue({
    system: { os: "linux", os_version: "Linux", arch: "x86_64", ram_gb: 64,
      disk_free_gb: 500, hostname: "test", wsl2_available: null, wsl2_installed: null },
    requirements: [], docker: { installed: true, running: true, version: "test",
      compose_installed: true, compose_version: "test" },
  });
  const prerequisites = {
    git_installed: true, docker_installed: true, docker_running: true,
    compose_installed: true, compose_version: "test",
    wsl2_needed: false, wsl2_installed: true, all_met: true,
  };
  vi.mocked(checkPrerequisites).mockResolvedValue(prerequisites);
  vi.mocked(detectGpu).mockResolvedValue({
    gpu: { vendor: "nvidia", name: "Test GPU", vram_mb: 24576, driver_version: null },
    recommended_tier: 3, tier_description: "Tier 3",
  });
  vi.mocked(startInstall).mockRejectedValueOnce(new Error("Fixture install failure"))
    .mockImplementation(() => new Promise(() => {}));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

async function continueChecks() {
  for (const heading of ["System Check", "All Prerequisites Met"]) {
    await screen.findByRole("heading", { name: heading });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  }
  await screen.findByRole("heading", { name: "GPU Detected" });
}

it("keeps the chosen tier and optional features through a failed-install retry", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
  await continueChecks();
  fireEvent.click(screen.getByRole("button", { name: "Tier 4" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByText("Voice", { exact: true }));
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  await screen.findByRole("heading", { name: "Something Went Wrong" });
  expect(startInstall).toHaveBeenLastCalledWith(4, expect.arrayContaining(["voice"]), undefined);
  fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
  await continueChecks();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  await waitFor(() => expect(startInstall).toHaveBeenCalledTimes(2));
  expect(startInstall).toHaveBeenLastCalledWith(4, expect.arrayContaining(["voice"]), undefined);
});

it("still uses the hardware recommendation before any user choice", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
  await continueChecks();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  await waitFor(() => expect(startInstall).toHaveBeenCalledTimes(1));
  expect(startInstall).toHaveBeenCalledWith(3, expect.any(Array), undefined);
});
