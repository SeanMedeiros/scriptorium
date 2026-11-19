import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Phase 6 on REAL data: two more TraPrInq validation-set pages (Zenodo 13986218) are added as
// train/validation ground truth next to the test page of 05-research; TraPrInq V1 (installed from
// Zenodo in 02-registry) is fine-tuned by the dedicated training worker; the new institutional model
// is evaluated on the held-out test page, packaged for publication, and the annotation, analytics,
// IIIF and training-data exports are exercised.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase6");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
const SAMPLES = path.resolve(__dirname, "../../var/samples/traprinq-val");
const TRAIN = "PT-TT-TSO-IL-28-687_m0019";
const VAL = "PT-TT-TSO-IL-28-12193_m0197";
const DL = path.resolve(__dirname, "../test-results/downloads");
fs.mkdirSync(DL, { recursive: true });

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

async function saveDownload(page: Page, click: () => Promise<void>): Promise<string> {
  const [dl] = await Promise.all([page.waitForEvent("download"), click()]);
  const p = path.join(DL, dl.suggestedFilename());
  await dl.saveAs(p);
  return p;
}

test.describe.serial("Advanced: training, institutional models, annotations, analytics, IIIF", () => {
  test.skip(!fs.existsSync(path.join(SAMPLES, `${TRAIN}.xml`)), "TraPrInq validation samples not available");

  test("add train and validation ground truth", async ({ page }) => {
    test.setTimeout(4 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
    await page.getByRole("button", { name: "Upload documents" }).click();
    await page.getByTestId("file-input").setInputFiles([path.join(SAMPLES, `${TRAIN}.jpg`), path.join(SAMPLES, `${VAL}.jpg`)]);
    await page.getByLabel(/Languages/).fill("por");
    await page.getByRole("button", { name: "Upload documents" }).last().click();
    await expect(page.getByTestId("document-table").getByText("Ready")).toHaveCount(4, { timeout: 90_000 });
    await openProject(page);
    await page.getByRole("tab", { name: "Evaluation" }).click();
    for (const [name, split] of [[TRAIN, "train"], [VAL, "validation"]] as const) {
      await page.getByRole("button", { name: "Add ground truth" }).click();
      const dlg = page.getByRole("dialog");
      await dlg.getByLabel("Collections").selectOption({ label: "Tribunal do Santo Ofício — Coimbra" });
      await dlg.getByLabel("Document").selectOption({ label: name });
      await expect(dlg.getByLabel("Page", { exact: true }).locator("option")).toHaveCount(2);
      await dlg.getByLabel("Page", { exact: true }).selectOption({ index: 1 });
      await dlg.getByLabel("Ground-truth file").setInputFiles(path.join(SAMPLES, `${name}.xml`));
      await dlg.getByLabel("Split", { exact: true }).selectOption(split);
      await dlg.getByRole("button", { name: "Add ground truth" }).click();
      await expect(dlg).toBeHidden();
    }
    await expect(page.getByTestId("gt-row")).toHaveCount(3);
  });

  test("real fine-tuning of TraPrInq on the training worker", async ({ page }) => {
    test.setTimeout(12 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "Training & models" }).click();
    await expect(page.getByText("No training job yet")).toBeVisible();
    await page.getByRole("button", { name: "New training job" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Ground-truth dataset").selectOption({ label: "Validação TraPrInq — amostra" });
    await expect(dlg).toContainText("train 1 · validation 1 · test 1 pages");
    const base = dlg.getByLabel("Base model");
    const tp = await base.locator("option", { hasText: "TraPrInq" }).getAttribute("value");
    await base.selectOption(tp!);
    await dlg.getByLabel("Model name").fill("TraPrInq — Coimbra");
    await dlg.getByLabel("Stop condition").selectOption("fixed");
    await dlg.getByLabel("Epochs", { exact: true }).fill("1");
    await dlg.getByRole("button", { name: "Start training" }).click();
    const row = page.getByTestId("training-row").first();
    await expect(row).toHaveAttribute("data-status", /queued|preparing|training/);
    await expect(row).toHaveAttribute("data-status", "completed", { timeout: 9 * 60_000 });
    await expect(row.getByTestId("training-test-cer")).toHaveText(/\d+\.\d\d %/, { timeout: 2 * 60_000 });
    await expect(page.locator('[data-testid="inst-model-row"][data-status="validated"]')).toHaveCount(1);
    await shot(page, "01-training-list");
    await row.getByRole("link", { name: "TraPrInq — Coimbra" }).click();
    await expect(page.getByTestId("epoch-row")).toHaveCount(1);
    await expect(page.getByTestId("checkpoint-row")).toHaveCount(1);
    await expect(page.getByTestId("training-output")).toContainText("TraPrInq — Coimbra · 1");
    await expect(page.getByTestId("training-log")).toContainText("model registered and validated by Kraken");
    await shot(page, "02-training-job");
  });

  test("publication package after PI approval", async ({ page }) => {
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "Training & models" }).click();
    const m = page.locator('[data-testid="inst-model-row"]').first();
    await expect(m).toContainText("Fine-tuned from TraPrInq");
    await m.getByRole("button", { name: "Prepare publication" }).click();
    let dlg = page.getByRole("dialog");
    await dlg.getByLabel("Description").fill("Kraken model fine-tuned from TraPrInq V1 on Coimbra Inquisition ground truth (SCRIPTORIUM).");
    await dlg.getByLabel("Authors").fill("Maria Helena Coelho; Universidade de Coimbra");
    await dlg.getByRole("button", { name: "Save draft" }).click();
    await m.getByRole("button", { name: "Approve publication" }).click();
    dlg = page.getByRole("dialog");
    await dlg.getByLabel("Approval statement").fill("All listed authors approved this publication.");
    await dlg.getByRole("button", { name: "Approve and build package" }).click();
    const zip = await saveDownload(page, () => m.getByRole("button", { name: "Zenodo package" }).click());
    expect(fs.readFileSync(zip).subarray(0, 2).toString()).toBe("PK");
    await shot(page, "03-institutional-models");
  });

  test("annotations, analytics, IIIF manifest and training-data export", async ({ page }) => {
    test.setTimeout(5 * 60_000);
    await login(page);
    await openProject(page);
    await page.getByRole("tab", { name: "Annotations" }).click();
    await page.getByLabel("Status").selectOption("");
    await expect(page.getByTestId("annotation-row").first()).toBeVisible();
    const w3c = await saveDownload(page, () => page.getByRole("button", { name: "W3C Web Annotations" }).click());
    const coll = JSON.parse(fs.readFileSync(w3c, "utf8"));
    expect(coll.type).toBe("AnnotationCollection");
    expect(coll.first.items.length).toBeGreaterThan(0);
    await shot(page, "04-annotations");

    await page.getByRole("tab", { name: "Evaluation" }).click();
    const ax = page.getByTestId("analytics");
    const sel = ax.getByLabel("Model");
    const tp = await sel.locator("option", { hasText: /^TraPrInq - / }).getAttribute("value");
    await sel.selectOption(tp!);
    await expect(ax.getByTestId("confusion-row").first()).toBeVisible();
    await expect(ax.getByTestId("reliability")).toBeVisible();
    await expect(ax.getByTestId("ece")).toContainText(/ECE: \d\.\d{3}/);
    await ax.scrollIntoViewIfNeeded();
    await shot(page, "05-analytics");

    await page.getByRole("button", { name: "Export training data (PAGE)" }).click();
    await expect(page.getByTestId("tde-msg")).toBeVisible();
    await page.getByRole("tab", { name: "Exports" }).click();
    const row = page.locator('[data-testid="export-row"]').first();
    await expect(row).toContainText("Training data");
    await expect(row).toHaveAttribute("data-status", "completed", { timeout: 3 * 60_000 });
    await expect(row.getByTestId("export-validation")).toContainText("3 XML files valid");

    await page.getByRole("tab", { name: "Collections" }).click();
    await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
    await page.getByTestId("document-table").getByRole("link", { name: "PT-TT-TSO-IL-28-7926_m0052" }).click();
    const manifest = await saveDownload(page, () => page.getByRole("button", { name: "IIIF manifest" }).click());
    const mf = JSON.parse(fs.readFileSync(manifest, "utf8"));
    expect(mf["@context"]).toBe("http://iiif.io/api/presentation/3/context.json");
    expect(mf.items[0].annotations[0].items.length).toBeGreaterThan(10);
  });
});
