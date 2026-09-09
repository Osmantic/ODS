// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Complete from "./Complete";
import { openODSserver } from "../hooks/useTauri";

vi.mock("../hooks/useTauri", () => ({ openODSserver: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("reports a failed browser launch and lets the user retry", async () => {
  vi.mocked(openODSserver).mockRejectedValueOnce(new Error("xdg-open unavailable")).mockResolvedValueOnce(null);
  render(<Complete />);
  fireEvent.click(screen.getByRole("button", { name: "Open ODS" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText(/open the Chat UI address above manually/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open ODS" }));
  await act(async () => {});
  expect(screen.queryByRole("alert")).toBeNull();
  expect(openODSserver).toHaveBeenCalledTimes(2);
});

it("launches one browser operation while the native command is pending", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(openODSserver).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<Complete />);
  const button = screen.getByRole("button", { name: "Open ODS" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect((screen.getByRole("button", { name: "Opening..." }) as HTMLButtonElement).disabled).toBe(true);
  expect(openODSserver).toHaveBeenCalledTimes(1);
  await act(async () => finish(null));
  expect((screen.getByRole("button", { name: "Open ODS" }) as HTMLButtonElement).disabled).toBe(false);
});
