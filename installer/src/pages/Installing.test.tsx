// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Installing from "./Installing";
import { getInstallProgress, startInstall } from "../hooks/useTauri";

vi.mock("../hooks/useTauri", () => ({
  startInstall: vi.fn(),
  getInstallProgress: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const progress = {
  phase: "images", percent: 48, message: "Downloading", error: null,
};

describe("installation progress subscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
    vi.mocked(getInstallProgress).mockResolvedValue(progress);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("keeps polling under StrictMode and starts the installer once", async () => {
    const install = deferred<string>();
    vi.mocked(startInstall).mockReturnValue(install.promise);
    const onComplete = vi.fn();
    render(<StrictMode><Installing tier={1} features={[]}
      onComplete={onComplete} onError={vi.fn()} /></StrictMode>);

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("48%")).toBeTruthy();
    expect(startInstall).toHaveBeenCalledTimes(1);
    await act(async () => install.resolve("done"));
    expect(onComplete).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(getInstallProgress).toHaveBeenCalledTimes(1);
  });

  it("resubscribes after a parent render without restarting the install", async () => {
    const install = deferred<string>();
    vi.mocked(startInstall).mockReturnValue(install.promise);
    const oldComplete = vi.fn();
    const newComplete = vi.fn();
    const view = render(<Installing tier={1} features={[]}
      onComplete={oldComplete} onError={vi.fn()} />);
    view.rerender(<Installing tier={1} features={[]}
      onComplete={newComplete} onError={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("48%")).toBeTruthy();
    await act(async () => install.resolve("done"));
    expect(oldComplete).not.toHaveBeenCalled();
    expect(newComplete).toHaveBeenCalledTimes(1);
    expect(startInstall).toHaveBeenCalledTimes(1);
  });

  it("does not overlap slow polls or publish after unmount", async () => {
    const install = deferred<string>();
    const poll = deferred<typeof progress>();
    vi.mocked(startInstall).mockReturnValue(install.promise);
    vi.mocked(getInstallProgress).mockReturnValue(poll.promise);
    const onComplete = vi.fn();
    const onError = vi.fn();
    const view = render(<Installing tier={1} features={[]}
      onComplete={onComplete} onError={onError} />);
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(getInstallProgress).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => {
      poll.resolve(progress);
      install.resolve("done");
    });
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(getInstallProgress).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports a polling outage and recovers without ending the installation", async () => {
    const install = deferred<string>();
    vi.mocked(startInstall).mockReturnValue(install.promise);
    vi.mocked(getInstallProgress).mockRejectedValueOnce(new Error("IPC unavailable"));
    const onError = vi.fn();
    render(<Installing tier={1} features={[]} onComplete={vi.fn()} onError={onError} />);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText(/Progress unavailable/)).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("48%")).toBeTruthy();
    expect(screen.queryByText(/Progress unavailable/)).toBeNull();
    expect(onError).not.toHaveBeenCalled();
  });

  it("uses the command result for failure and cancels subsequent polling", async () => {
    const install = deferred<string>();
    vi.mocked(startInstall).mockReturnValue(install.promise);
    const onError = vi.fn();
    render(<StrictMode><Installing tier={1} features={[]}
      onComplete={vi.fn()} onError={onError} /></StrictMode>);
    await act(async () => install.reject(new Error("installer exited 1")));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith("Error: installer exited 1");
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(getInstallProgress).not.toHaveBeenCalled();
  });
});
