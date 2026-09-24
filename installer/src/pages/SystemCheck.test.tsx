// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import SystemCheck from "./SystemCheck";
import { checkSystem, type SystemCheckResult } from "../hooks/useTauri";
vi.mock("../hooks/useTauri", () => ({ checkSystem: vi.fn() }));
afterEach(cleanup);
beforeEach(() => vi.resetAllMocks());

function result(met: boolean): SystemCheckResult {
  return {
    system: { os: "linux", os_version: "Linux", arch: "x86_64", ram_gb: 32,
      disk_free_gb: met ? 100 : 1, hostname: "test", wsl2_available: null, wsl2_installed: null },
    requirements: [{ name: "Disk space", met, found: met ? "100 GB free" : "1 GB free",
      required: "50 GB", help: null }],
    docker: { installed: true, running: met, version: "Docker test",
      compose_installed: true, compose_version: "v2" },
  };
}

it("rechecks after freeing disk space and starting Docker", async () => {
  let finish!: (value: SystemCheckResult) => void;
  vi.mocked(checkSystem).mockResolvedValueOnce(result(false))
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const next = vi.fn();
  render(<SystemCheck onNext={next} onError={vi.fn()} />);
  await screen.findByText("1 GB free");
  fireEvent.click(screen.getByRole("button", { name: "Check Again" }));
  await waitFor(() => expect(checkSystem).toHaveBeenCalledTimes(2));
  expect((screen.getByRole("button", { name: "Continue Anyway" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Checking..." }) as HTMLButtonElement).disabled).toBe(true);
  finish(result(true));
  await screen.findByText("100 GB free");
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(next).toHaveBeenCalledTimes(1);
});

it("keeps the previous snapshot and permits retry after a refresh failure", async () => {
  vi.mocked(checkSystem).mockResolvedValueOnce(result(false))
    .mockRejectedValueOnce("Probe unavailable").mockResolvedValueOnce(result(true));
  const error = vi.fn();
  render(<SystemCheck onNext={vi.fn()} onError={error} />);
  fireEvent.click(await screen.findByRole("button", { name: "Check Again" }));
  await screen.findByRole("alert");
  expect(screen.getByText("1 GB free")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("Previous results");
  expect(error).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check Again" }));
  await screen.findByText("100 GB free");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("does not navigate to an error after leaving a pending check", async () => {
  let fail!: (reason: string) => void;
  vi.mocked(checkSystem).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
  const error = vi.fn();
  const view = render(<SystemCheck onNext={vi.fn()} onError={error} />);
  view.unmount();
  fail("Late failure");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(error).not.toHaveBeenCalled();
});
