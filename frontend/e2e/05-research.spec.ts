import { expect, test, type Download, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Research tools on REAL data: the Torre do Tombo page recognised in 03-engine, its real
// Transkribus ground truth (TraPrInq validation set, Zenodo 13986218), and the models
// installed from Zenodo in 02-registry (TraPrInq V1, CATMuS Medieval 1.6.2).
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase5");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
const GT_XML = path.resolve(__dirname, "../../var/samples/traprinq-val/PT-TT-TSO-IL-28-7926_m0052.xml");
const DOWNLOADS = path.resolve(__dirname, "../test-results/downloads");
fs.mkdirSync(DOWNLOADS, { recursive: true });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("E-mail address").fill("mhc@uc.pt");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function openProject(page: Page) {
  await page.getByRole("link", { name: "Projects" }).click();
  await page.getByRole("link", { name: /Processos da Inquisição de Coimbra/ }).click();
}

async function save(dl: Download): Promise<string> {
  const p = path.join(DOWNLOADS, dl.suggestedFilename());
  await dl.saveAs(p);
  return p;
}

test.describe.serial("Research tools", () => {
  test.skip(!fs.existsSync(GT_XML), "TraPrInq validation ground truth not available");
  let combinedUrl = "";

  test("ground truth import and real evaluation of two Zenodo models", async ({ page }) => {
    test.setTimeout(8 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "Evaluation" }).click();
    await expect(page.getByText("No evaluation dataset yet")).toBeVisible();
    await page.getByRole("button", { name: "New dataset" }).click();
    let dlg = page.getByRole("dialog");
    await dlg.getByLabel("Name").fill("Validação TraPrInq — amostra");
    await dlg.getByRole("button", { name: "Create" }).click();
    await expect(page.getByTestId("dataset-detail")).toContainText("Validação TraPrInq — amostra");

    await page.getByRole("button", { name: "Add ground truth" }).click();
    dlg = page.getByRole("dialog");
    await dlg.getByLabel("Collections").selectOption({ label: "Tribunal do Santo Ofício — Coimbra" });
    await dlg.getByLabel("Document").selectOption({ label: "PT-TT-TSO-IL-28-7926_m0052" });
    await dlg.getByLabel("Page", { exact: true }).selectOption({ index: 1 });
    await dlg.getByLabel("Ground-truth file").setInputFiles(GT_XML);
    await dlg.getByLabel("Note").fill("TraPrInq Validation_Set_FINAL; overlap with the model's training data is not documented");
    await dlg.getByRole("button", { name: "Add ground truth" }).click();
    const gt = page.getByTestId("gt-row");
    await expect(gt).toHaveCount(1);
    await expect(gt).toContainText("ALTO");
    await expect(gt).toContainText("18");

    await page.getByRole("button", { name: "Evaluate models" }).click();
    dlg = page.getByRole("dialog");
    await dlg.getByLabel(/TraPrInq .* V1/).check();
    await dlg.getByLabel("CATMuS Medieval · 1.6.2").check();
    await expect(dlg.getByLabel("Unicode normalisation")).toHaveValue("NFC");
    await dlg.getByRole("button", { name: "Evaluate models" }).click();
    const runs = page.getByTestId("run-row");
    await expect(runs).toHaveCount(2);
    await expect(page.locator('[data-testid="run-row"][data-status="completed"]')).toHaveCount(2, { timeout: 6 * 60_000 });
    for (const c of await page.getByTestId("run-cer").allTextContents()) expect(c).toMatch(/^\d+\.\d\d %$/);
    await page.reload();
    await page.getByRole("tab", { name: "Evaluation" }).click();
    await expect(page.getByTestId("group-row")).toHaveCount(2);
    await expect(page.getByTestId("cer-chart")).toBeVisible();
    await page.getByLabel("Group by").selectOption("language");
    await expect(page.getByTestId("group-row").first()).toContainText("por");
    await page.getByLabel("Group by").selectOption("model");
    await shot(page, "01-evaluation");
  });

  test("model comparison on the ground-truth segmentation and combined transcription", async ({ page }) => {
    test.setTimeout(6 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
    await page.getByTestId("document-table").getByRole("link", { name: "PT-TT-TSO-IL-28-7926_m0052" }).click();
    await page.getByTestId("page-grid").getByRole("link").first().click();
    await page.getByRole("link", { name: "Compare models" }).click();
    const segSel = page.getByLabel("Segmentation");
    const gtValue = await segSel.locator("option", { hasText: "Imported (ground truth)" }).getAttribute("value");
    await segSel.selectOption(gtValue!);
    await page.getByLabel(/TraPrInq .* V1/).check();
    await page.getByLabel("CATMuS Medieval · 1.6.2").check();
    await page.getByRole("button", { name: "Run comparison" }).click();
    await expect(page).toHaveURL(/\/comparisons\//);
    await expect(page.locator('[data-testid="cmp-model"][data-status="completed"]')).toHaveCount(2, { timeout: 4 * 60_000 });
    await expect(page.getByTestId("cmp-cer")).toHaveCount(2);
    await expect(page.getByTestId("cmp-line")).toHaveCount(18);
    await expect(page.getByTestId("pairwise")).toContainText("disagreement between models, not accuracy");
    await shot(page, "02-comparison");
    await page.getByRole("radio", { name: /Use line 1 from CATMuS Medieval 1\.6\.2/ }).check();
    await page.getByRole("button", { name: "Create combined transcription" }).click();
    await expect(page).toHaveURL(/\/transcriptions\//);
    combinedUrl = page.url();
    await page.getByLabel("Line 1", { exact: true }).click();
    await expect(page.getByTestId("line-details")).toContainText("(CATMuS Medieval · 1.6.2)");
    await page.getByLabel("Line 2", { exact: true }).click();
    await expect(page.getByTestId("line-details")).toContainText("TraPrInq");
  });

  test("single-page exports are validated; provenance is complete", async ({ page }) => {
    await login(page);
    // the combined transcription (default model TraPrInq; line 1 taken from CATMuS)
    await page.goto(combinedUrl);
    await page.getByRole("button", { name: "Provenance" }).click();
    const pv = page.getByTestId("provenance");
    await expect(pv).toContainText("10.5281/zenodo.14543904");
    await expect(pv).toContainText("cc-by-4.0");
    await expect(pv).toContainText("source-reported, not measured here");
    await expect(pv).toContainText("PT/TT/TSO-IL/28/7926");
    await expect(pv).toContainText("catmus-medieval 1.6.2 + traprinq V1");
    await shot(page, "03-provenance");
    await page.keyboard.press("Escape");
    for (const [fmt, label] of [
      ["tei", "TEI P5 XML"],
      ["page", "PAGE XML 2019"],
      ["alto", "ALTO 4.4 XML"],
    ] as const) {
      await page.getByRole("button", { name: "Export" }).click();
      const dlg = page.getByRole("dialog");
      await dlg.getByLabel("Format").selectOption({ label });
      const [dl] = await Promise.all([page.waitForEvent("download"), dlg.getByRole("button", { name: "Download" }).click()]);
      const file = await save(dl);
      expect(fs.statSync(file).size).toBeGreaterThan(1000);
      await expect(dlg.getByTestId("validation-ok")).toBeVisible({ timeout: 60_000 });
      if (fmt === "tei") {
        expect(fs.readFileSync(file, "utf8")).toContain('<application ident="kraken" version="7.1.1">');
        await shot(page, "04-export-tei");
      }
      await page.keyboard.press("Escape");
    }
  });

  test("batch export of the collection with manifest; report and dashboard quality", async ({ page }) => {
    test.setTimeout(4 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "Exports" }).click();
    await page.getByRole("tabpanel", { name: "Exports" }).getByLabel("Collections").selectOption({ label: "Tribunal do Santo Ofício — Coimbra" });
    for (const f of ["ALTO 4.4 XML", "hOCR", "PDF", "Word (DOCX)"]) await page.getByLabel(f, { exact: true }).check();
    await page.getByRole("button", { name: "Start export" }).click();
    const row = page.locator('[data-testid="export-row"]').first();
    await expect(row).toHaveAttribute("data-status", "completed", { timeout: 3 * 60_000 });
    await expect(row.getByTestId("export-validation")).toContainText("XML files valid");
    await expect(row).toContainText("pages skipped (see manifest)"); // pages without any recognition are listed, not hidden
    const [dl] = await Promise.all([page.waitForEvent("download"), row.getByRole("button", { name: /ZIP/ }).click()]);
    const zip = await save(dl);
    expect(fs.readFileSync(zip).subarray(0, 2).toString()).toBe("PK");
    await shot(page, "05-exports");

    await page.getByRole("tab", { name: "Report" }).click();
    const rep = page.getByTestId("report");
    await expect(rep).toContainText("Quality against ground truth");
    await expect(rep).toContainText("Maria Helena");
    await expect(rep.getByTestId("group-row")).toHaveCount(2);
    await shot(page, "06-report");

    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page.getByTestId("dashboard-quality")).toContainText("CER");
    await expect(page.getByTestId("dashboard-quality")).toContainText("TraPrInq");
  });
});
