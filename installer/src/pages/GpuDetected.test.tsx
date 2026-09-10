// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import GpuDetected from "./GpuDetected";
import { detectGpu, type GpuResult } from "../hooks/useTauri";

vi.mock("../hooks/useTauri", () => ({ detectGpu: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const detected: GpuResult = {
  gpu: { vendor: "nvidia", name: "Test GPU", vram_mb: 24576, driver_version: null },
  recommended_tier: 3, tier_description: "Tier 3",
};

it("offers a working retry after the native GPU command rejects", async () => {
  vi.mocked(detectGpu).mockRejectedValueOnce(new Error("GPU command unavailable"));
  vi.mocked(detectGpu).mockResolvedValueOnce(detected);
  const onNext = vi.fn();
  render(<GpuDetected onNext={onNext} />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("Detecting GPU hardware...")).toBeNull();
  expect(onNext).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry GPU Check" }));
  expect(await screen.findByText("Test GPU")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(onNext).toHaveBeenCalledWith(3);
});

describe("superseded detection", () => {
  it.each(["resolve", "reject"] as const)(
    "ignores a late %s from the disposed StrictMode effect",
    async (outcome) => {
      let resolve!: (value: GpuResult) => void;
      let reject!: (reason: Error) => void;
      const oldRequest = new Promise<GpuResult>((res, rej) => { resolve = res; reject = rej; });
      vi.mocked(detectGpu).mockReturnValueOnce(oldRequest).mockResolvedValueOnce(detected);
      const onNext = vi.fn();
      render(<StrictMode><GpuDetected onNext={onNext} /></StrictMode>);
      expect(await screen.findByText("Test GPU")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Tier 1" }));
      await act(async () => {
        if (outcome === "resolve") resolve({ ...detected, recommended_tier: 4 });
        else reject(new Error("stale failure"));
      });
      expect(screen.queryByRole("alert")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      expect(onNext).toHaveBeenCalledWith(1);
    },
  );
});
