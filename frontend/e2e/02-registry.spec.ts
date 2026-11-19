import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Model registry, end to end against the REAL Zenodo service: preview with licence,
// licence acceptance, real downloads (TraPrInq ≈160 MB, CATMuS Medieval ≈16 MB),
// checksum verification, Kraken validation, versions and rollback.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase3");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("E-mail address").fill("mhc@uc.pt");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function installFromCatalog(page: Page, family: string, expect_: { title: RegExp; checksum: string; licence: string }) {
  await page.getByRole("link", { name: "Models" }).click();
  await page.getByRole("tab", { name: "Install from Zenodo" }).click();
  await page.getByRole("button", { name: family, exact: true }).click();
  const pv = page.getByTestId("registry-preview");
  await expect(pv).toBeVisible({ timeout: 150_000 /* Zenodo API latency varies (62 s observed) */ });
  await expect(pv).toContainText(expect_.title);
  await expect(pv).toContainText(expect_.checksum);
  await expect(pv).toContainText(expect_.licence);
  const install = pv.getByRole("button", { name: "Download and install" });
  await expect(install).toBeDisabled(); // licence not yet accepted
  return { pv, install };
}

test.describe.serial("Model registry (real Zenodo)", () => {
  test("administrator previews and installs TraPrInq from Zenodo", async ({ page }) => {
    test.setTimeout(10 * 60_000);
    await login(page);
    const { pv, install } = await installFromCatalog(page, "TraPrInq", {
      title: /TraPrInq/, checksum: "md5:b7f84ede75a0665c275346d94fdc1b95", licence: "cc-by-4.0",
    });
    await expect(pv).toContainText("Weslley Oliveira");
    await expect(pv).toContainText("94.87% *"); // source-reported, marked as such
    await expect(pv).toContainText("NFD");
    await expect(pv).toContainText("Likely compatible");
    await shot(page, "01-preview-traprinq");
    await pv.getByRole("checkbox").check();
    await install.click();
    await expect(page.getByText("Installation started")).toBeVisible({ timeout: 150_000 }); // the API consults Zenodo synchronously (retries on 5xx)

    await page.getByRole("tab", { name: "Models", exact: true }).click();
    const card = page.locator('[data-testid^="model-traprinq"]');
    await expect(card.locator('[data-status="downloading"], [data-status="installing"], [data-status="validated"]').first()).toBeVisible({ timeout: 60_000 });
    if (await card.locator('[data-status="downloading"]').count()) await shot(page, "02-download-progress");
    await expect(card.locator('[data-status="validated"]')).toBeVisible({ timeout: 9 * 60_000 });
    await expect(card).toContainText("Loaded with Kraken 7.1.1");
    await expect(card).toContainText("Active");
    await expect(card).toContainText("Curated by an administrator");
    await card.getByRole("button", { name: "Installation log" }).click();
    const log = page.getByTestId("install-log");
    await expect(log).toContainText("download complete; size and checksum match the Zenodo record");
    await expect(log).toContainText("validated: model loaded by Kraken");
    await shot(page, "03-traprinq-validated");
    await page.keyboard.press("Escape");
  });

  test("CATMuS Medieval: latest version via concept DOI, then an older version and rollback", async ({ page }) => {
    test.setTimeout(8 * 60_000);
    await login(page);
    const { pv, install } = await installFromCatalog(page, "CATMuS", {
      title: /CATMuS Medieval/, checksum: "md5:326a6ea675661ec9ce0bb6b1958de495", licence: "cc-by-4.0",
    });
    const versions = pv.getByTestId("preview-versions");
    await expect(versions).toContainText("1.5.0");
    await expect(versions).toContainText("latest");
    await expect(versions).toContainText("same model file as 1.6.0");
    await expect(pv).toContainText("valid (htrmopo-v1)");
    await shot(page, "04-preview-catmus");
    await pv.getByRole("checkbox").check();
    await install.click();
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    const card = page.locator('[data-testid^="model-catmus-medieval"]');
    await expect(card.locator('[data-status="validated"]')).toBeVisible({ timeout: 5 * 60_000 });
    await expect(card).toContainText("lat · oci"); // languages declared by the source model card

    // Install 1.5.0 explicitly; the active version must not change.
    await page.getByRole("tab", { name: "Install from Zenodo" }).click();
    await page.getByLabel("Zenodo DOI, record URL or record id").fill("10.5281/zenodo.12743230");
    await page.getByRole("button", { name: "Preview" }).click();
    const pv2 = page.getByTestId("registry-preview");
    await expect(pv2).toContainText("This is not the latest version of the record.", { timeout: 150_000 });
    await pv2.getByRole("checkbox").check();
    await pv2.getByRole("button", { name: "Download and install" }).click();
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    const v150 = card.locator('tr[data-version="1.5.0"]');
    await expect(v150.locator('[data-status="validated"]')).toBeVisible({ timeout: 5 * 60_000 });
    await expect(v150).not.toContainText("Active");
    // Roll back to 1.5.0, then return to the latest.
    await v150.getByRole("button", { name: "Activate" }).click();
    await expect(v150).toContainText("Active");
    await shot(page, "05-catmus-rollback");
    await card.locator("tr").filter({ hasNot: page.locator('[data-version="1.5.0"]') }).getByRole("button", { name: "Activate" }).first().click();
    await expect(v150).not.toContainText("Active");
    await card.getByRole("button", { name: "Check for updates" }).click();
    await expect(card).toContainText("Up to date", { timeout: 150_000 }); // update check queries Zenodo (retries on 5xx)
  });

  test("discovery lists CATMuS records with independent compatibility verdicts", async ({ page }) => {
    await login(page);
    await page.getByRole("link", { name: "Models" }).click();
    await page.getByRole("tab", { name: "Discover on Zenodo" }).click();
    await page.getByRole("button", { name: "Search" }).click();
    const res = page.getByTestId("discover-results");
    await expect(res).toContainText("CATMuS", { timeout: 60_000 });
    await expect(res.getByText("Likely compatible").first()).toBeVisible();
    await shot(page, "06-discovery");
  });
});
