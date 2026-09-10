import { expect, test } from "@playwright/test";

for (const viewport of [{ width: 800, height: 600 }, { width: 480, height: 400 }]) {
  test(`wizard content stays reachable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      Object.defineProperty(window, "__TAURI_INTERNALS__", {
        value: { invoke: async (command: string) => {
          switch (command) {
            case "check_system":
              return {
                system: { os_version: "Linux test fixture", arch: "x86_64" },
                requirements: [],
                docker: { installed: true, running: true, version: "fixture" },
              };
            case "check_prerequisites":
              return { all_met: true, wsl2_needed: false };
            case "detect_gpu":
              return {
                gpu: { vendor: "nvidia", name: "Test GPU", vram_mb: 24576 },
                recommended_tier: 3, tier_description: "Test tier",
              };
            case "start_install":
              throw new Error("Test installation failure");
            default:
              throw new Error(`Unexpected test command: ${command}`);
          }
        } },
      });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "ODS", exact: true })).toBeInViewport({ ratio: 1 });
    await page.getByRole("button", { name: "Get Started" }).click();

    for (const heading of ["System Check", "All Prerequisites Met", "GPU Detected"]) {
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeInViewport({ ratio: 1 });
      await page.getByRole("button", { name: "Continue", exact: true }).click();
    }

    const features = page.getByRole("heading", { name: "Choose Features" });
    await expect(features).toBeInViewport({ ratio: 1 });
    // Scroll to the last action and back: the heading must be reachable,
    // not positioned above the scroll container's zero position.
    await page.getByRole("button", { name: "Select All" }).scrollIntoViewIfNeeded();
    await features.scrollIntoViewIfNeeded();
    await expect(features).toBeInViewport({ ratio: 1 });
    await page.getByRole("button", { name: "Install", exact: true }).click();
    await page.getByRole("heading", { name: "Something Went Wrong" }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("heading", { name: "Something Went Wrong" })).toBeInViewport({ ratio: 1 });
    await page.getByRole("button", { name: "Try Again" }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "Try Again" })).toBeInViewport({ ratio: 1 });
  });
}
