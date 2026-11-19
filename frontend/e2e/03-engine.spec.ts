import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Phase 2 end-to-end: real upload, real PDF page extraction, REAL Kraken HTR with
// TraPrInq on a 16th-century Inquisition page, executed by a real Celery worker.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase2");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
const JPG = process.env.E2E_SAMPLE_JPG;
const PDF = process.env.E2E_SAMPLE_PDF;

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("E-mail address").fill("mhc@uc.pt");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function openCollection(page: Page) {
  await page.getByRole("link", { name: "Projects" }).click();
  await page.getByRole("link", { name: /Processos da Inquisição de Coimbra/ }).click();
  await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
  await expect(page.getByRole("heading", { name: "Tribunal do Santo Ofício — Coimbra" })).toBeVisible();
}

test.describe.serial("Kraken engine", () => {
  test.skip(!JPG || !PDF, "real manuscript samples not available");
  test.setTimeout(3 * 60_000);

  test("upload a manuscript image and a multi-page PDF", async ({ page }) => {
    await login(page);
    await openCollection(page);
    await page.getByRole("button", { name: "Upload documents" }).click();
    await page.getByTestId("file-input").setInputFiles([JPG!, PDF!]);
    await page.getByLabel(/Languages/).fill("por");
    await page.getByLabel(/Archival reference/).fill("PT/TT/TSO-IL/28/7926");
    await page.getByRole("button", { name: "Upload documents" }).last().click();
    const table = page.getByTestId("document-table");
    await expect(table.getByText("PT-TT-TSO-IL-28-7926_m0052", { exact: true })).toBeVisible();
    await expect(table.getByText("Ready")).toHaveCount(2, { timeout: 60_000 });
    await expect(table.getByRole("row", { name: /inquisicao-3-folios/ })).toContainText("3");
    await shot(page, "02-collection-documents");

    await table.getByRole("link", { name: "inquisicao-3-folios" }).click();
    await expect(page.getByTestId("page-grid").getByRole("listitem")).toHaveCount(3);
    await expect(page.getByTestId("page-grid").locator("img").first()).toBeVisible();
  });

  test("real HTR job: segmentation + TraPrInq recognition by the worker", async ({ page }) => {
    test.setTimeout(8 * 60_000);
    await login(page);
    await openCollection(page);
    await page.getByTestId("document-table").getByRole("link", { name: "PT-TT-TSO-IL-28-7926_m0052" }).click();
    await page.getByRole("button", { name: "Run recognition" }).click();
    // Recommendation from declared/curated metadata (document: Portuguese manuscript).
    const rec = page.getByTestId("recommendation");
    await expect(rec.getByRole("listitem").first()).toContainText("TraPrInq");
    await expect(rec.getByRole("listitem").first()).toContainText("language matches (curated)");
    await expect(rec).toContainText("does not establish");
    await rec.getByRole("listitem").first().getByRole("button", { name: "Use" }).click();
    await expect(page.getByLabel("Recognition model")).toContainText("TraPrInq");
    await page.getByRole("button", { name: /Submit 1 job/ }).click();
    await expect(page.getByText("1 job(s) queued.")).toBeVisible();
    const jobs = page.getByTestId("job-table");
    await expect(jobs.locator('[data-status="processing"], [data-status="completed"]').first()).toBeVisible({ timeout: 60_000 });
    await shot(page, "03-job-processing");
    await expect(jobs.locator('[data-status="completed"]')).toBeVisible({ timeout: 7 * 60_000 });
    await shot(page, "04-job-completed");

    await jobs.getByRole("button", { name: "Log" }).click();
    for (const m of ["job started", "segmentation finished", "recognition finished", "job completed"]) {
      await expect(page.getByRole("dialog").getByText(m, { exact: false }).first()).toBeVisible();
    }
    await page.keyboard.press("Escape");

    await jobs.getByRole("link", { name: "View result" }).click();
    const lines = page.getByTestId("recognised-lines").getByRole("listitem");
    await expect(lines.first()).toBeVisible();
    expect(await lines.count()).toBeGreaterThan(15);
    const polys = page.getByTestId("line-overlay").locator("polygon");
    expect(await polys.count()).toBe(await lines.count());
    // Text ↔ image synchronisation: selecting a line highlights its polygon.
    const third = lines.nth(2);
    await third.click();
    const lineId = await third.getAttribute("data-line-id");
    await expect(page.locator(`polygon[data-line-id="${lineId}"]`)).toHaveClass(/fill-\[#23395b\]\/25/);
    const prov = page.getByTestId("provenance");
    await expect(prov).toContainText("TraPrInq");
    await expect(prov).toContainText("NFD");
    await expect(prov).toContainText("kraken 7.1.1");
    await expect(prov).toContainText("grayscale");
    await shot(page, "05-page-viewer-htr");
  });

  test("batch submission and cancellation of a queued job", async ({ page }) => {
    await login(page);
    await openCollection(page);
    await page.getByTestId("document-table").getByRole("link", { name: "inquisicao-3-folios" }).click();
    await page.getByRole("button", { name: "Run recognition" }).click();
    await page.getByLabel("Processing").selectOption("segment");
    await page.getByRole("button", { name: /Submit 3 job/ }).click();
    const jobs = page.getByTestId("job-table");
    await expect(jobs.getByRole("row")).toHaveCount(4); // header + 3 jobs
    const queuedRow = jobs.getByRole("row").filter({ has: page.locator('[data-status="queued"]') }).first();
    await queuedRow.getByRole("button", { name: "Cancel" }).click();
    await expect(jobs.locator('[data-status="cancelled"]')).toHaveCount(1);
    await expect(page.getByTestId("job-counts")).toContainText("Cancelled: 1");
    await shot(page, "06-batch-cancel");
  });

  test("dashboard reflects processed pages from the database", async ({ page }) => {
    await login(page);
    const p = page.getByTestId("dashboard-processing");
    await expect(p.locator("div", { hasText: /^Documents2$/ })).toBeVisible();
    await expect(p.locator("div", { hasText: /^Pages4$/ })).toBeVisible();
    await expect(p.locator("div", { hasText: /^Processed pages1$/ })).toBeVisible();
    await shot(page, "07-dashboard");
  });
});
