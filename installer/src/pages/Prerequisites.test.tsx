// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Prerequisites from "./Prerequisites";
import { checkPrerequisites, installPrerequisite, type InstallPrereqResult } from "../hooks/useTauri";

vi.mock("../hooks/useTauri", () => ({
  checkPrerequisites: vi.fn(), installPrerequisite: vi.fn(),
}));
const composeReady = { compose_installed: true, compose_version: "test" };
const missing = {
  git_installed: true, docker_installed: false, docker_running: false,
  compose_installed: false, compose_version: null,
  wsl2_needed: false, wsl2_installed: true, all_met: false,
};
beforeEach(() => {
  vi.mocked(checkPrerequisites).mockResolvedValue(missing);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it.each(["docker", "wsl2"] as const)(
  "can retry %s after an unsuccessful installation receipt",
  async (component) => {
    if (component === "wsl2") {
      vi.mocked(checkPrerequisites).mockResolvedValue({
        ...missing, ...composeReady, wsl2_needed: true, wsl2_installed: false,
        docker_installed: true, docker_running: true,
      });
    }
    vi.mocked(installPrerequisite)
      .mockResolvedValueOnce({ success: false, message: "Download failed", reboot_required: false })
      .mockResolvedValueOnce({ success: true, message: "Setup finished", reboot_required: false });
    render(<Prerequisites onNext={vi.fn()} onError={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Install" }));
    fireEvent.click(await screen.findByRole("button", { name: /Retry/ }));
    expect(await screen.findByText("Setup finished")).toBeTruthy();
    expect(vi.mocked(installPrerequisite).mock.calls).toEqual([[component], [component]]);
  },
);

it("can retry when the native install command rejects", async () => {
  vi.mocked(installPrerequisite)
    .mockRejectedValueOnce(new Error("Native command failed"))
    .mockResolvedValueOnce({ success: true, message: "Setup finished", reboot_required: false });
  render(<Prerequisites onNext={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Install" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry Docker" }));
  expect(await screen.findByText("Setup finished")).toBeTruthy();
});

it("surfaces re-check errors and permits a fresh readiness check", async () => {
  vi.mocked(checkPrerequisites)
    .mockResolvedValueOnce(missing)
    .mockRejectedValueOnce(new Error("IPC unavailable"))
    .mockResolvedValueOnce({ ...missing, ...composeReady, docker_installed: true, docker_running: true, all_met: true });
  const onError = vi.fn();
  render(<Prerequisites onNext={vi.fn()} onError={onError} />);
  fireEvent.click(await screen.findByRole("button", { name: "Re-check" }));
  expect(await screen.findByText(/Could not re-check prerequisites/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Re-check" }));
  expect(await screen.findByText("All Prerequisites Met")).toBeTruthy();
  expect(onError).not.toHaveBeenCalled();
});

it("prevents concurrent prerequisite installation and readiness checks", async () => {
  vi.mocked(checkPrerequisites).mockResolvedValue({
    ...missing, wsl2_needed: true, wsl2_installed: false,
  });
  let finish!: (value: InstallPrereqResult) => void;
  vi.mocked(installPrerequisite).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<Prerequisites onNext={vi.fn()} onError={vi.fn()} />);
  const installButtons = await screen.findAllByRole("button", { name: "Install" });
  fireEvent.click(installButtons[0]);
  fireEvent.click(installButtons[1]);
  fireEvent.click(screen.getByRole("button", { name: "Re-check" }));
  expect(installPrerequisite).toHaveBeenCalledTimes(1);
  expect(checkPrerequisites).toHaveBeenCalledTimes(1);
  await act(async () => finish({ success: false, message: "Download failed", reboot_required: false }));
  expect((screen.getByRole("button", { name: "Re-check" }) as HTMLButtonElement).disabled).toBe(false);
});
