import { expect, test } from "@playwright/test";

for (const outcome of ["complete", "error"]) {
  test(`keyboard focus follows setup through ${outcome}`, async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "__TAURI_INTERNALS__", {
        value: { invoke: async (command: string) => {
          if (command === "check_system") return {
            system: { os_version: "Test Linux", arch: "x86_64" }, requirements: [],
            docker: { installed: true, running: true, version: "test" },
          };
          if (command === "check_prerequisites") return { all_met: true };
          if (command === "detect_gpu") return {
            gpu: { vendor: "nvidia", name: "Test GPU", vram_mb: 24576 },
            recommended_tier: 3, tier_description: "Test tier",
          };
          if (command === "get_install_progress") return {
            phase: "installing", percent: 25, message: "Installing", error: null,
          };
          if (command === "start_install") return new Promise((resolve, reject) => {
            Object.assign(window, {
              finishInstall: (success: boolean) => success
                ? resolve("done") : reject(new Error("Fixture failure")),
            });
          });
          throw new Error(`Unexpected command: ${command}`);
        } },
      });
    });
    await page.goto("/");
    const main = page.getByRole("main");
    await expect(main).toBeFocused();
    await expect(main).toHaveAccessibleName("ODS setup: Welcome");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Get Started" })).toBeFocused();
    await page.keyboard.press("Enter");

    for (const label of ["System check", "Prerequisites", "GPU selection"]) {
      await expect(main).toBeFocused();
      await expect(main).toHaveAccessibleName(`ODS setup: ${label}`);
      await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuetext", new RegExp(label));
      const next = page.getByRole("button", { name: "Continue", exact: true });
      await expect(next).toBeVisible();
      await next.focus();
      await page.keyboard.press("Enter");
    }
    await expect(main).toBeFocused();
    await expect(main).toHaveAccessibleName("ODS setup: Feature selection");
    await page.getByRole("button", { name: "Install", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(main).toBeFocused();
    await expect(main).toHaveAccessibleName("ODS setup: Installation");
    await page.evaluate((success) => {
      (window as unknown as { finishInstall: (success: boolean) => void }).finishInstall(success);
    }, outcome === "complete");
    await expect(main).toBeFocused();
    await expect(main).toHaveAccessibleName(`ODS setup: ${outcome === "complete" ? "Complete" : "Installation error"}`);
    if (outcome === "error") {
      await page.getByRole("button", { name: "Try Again" }).focus();
      await page.keyboard.press("Enter");
      await expect(main).toBeFocused();
      await expect(main).toHaveAccessibleName("ODS setup: System check");
    }
  });
}
