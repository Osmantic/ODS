// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ErrorPage from "./ErrorPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function clipboard(writeText?: (text: string) => Promise<void>) {
  vi.stubGlobal("navigator", {
    platform: "Linux", userAgent: "ODS test",
    clipboard: writeText ? { writeText } : undefined,
  });
}

describe("installation failure diagnostics", () => {
  it("waits for the clipboard and prevents duplicate copies", async () => {
    let finish!: () => void;
    const write = vi.fn((_text: string) => new Promise<void>((resolve) => { finish = resolve; }));
    clipboard(write);
    render(<ErrorPage message={"Disk full\nInstall stopped"} onRetry={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy Diagnostics" }));
    const pending = screen.getByRole("button", { name: "Copying..." });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(pending);
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0]).toContain("Error: Disk full\nInstall stopped");
    finish();
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Diagnostics copied"));
  });

  it("keeps diagnostics selectable when the clipboard API is absent", async () => {
    clipboard();
    const retry = vi.fn();
    render(<ErrorPage message="Docker unavailable" onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy Diagnostics" }));
    const text = await screen.findByRole("textbox", { name: "Diagnostics to copy manually" });
    expect((text as HTMLTextAreaElement).value).toContain("Error: Docker unavailable");
    expect((text as HTMLTextAreaElement).readOnly).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("allows another copy attempt after permission is denied", async () => {
    const write = vi.fn()
      .mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"))
      .mockResolvedValueOnce(undefined);
    clipboard(write);
    render(<ErrorPage message="Install failed" onRetry={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy Diagnostics" }));
    await screen.findByRole("textbox", { name: "Diagnostics to copy manually" });
    expect(screen.getByRole("alert").textContent).toContain("Select and copy");
    fireEvent.click(screen.getByRole("button", { name: "Copy Diagnostics" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Diagnostics copied"));
    expect(write).toHaveBeenCalledTimes(2);
    expect(write.mock.calls[1][0]).toBe(write.mock.calls[0][0]);
  });
});
