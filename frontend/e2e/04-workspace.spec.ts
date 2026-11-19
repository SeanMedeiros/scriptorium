import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Transcription workspace on the REAL TraPrInq output produced by 03-engine.spec.ts:
// synchronised editing, markup, autosave revisions, undo/redo, history/restore,
// comments, concurrent-edit conflict, and layout adjustment → real re-recognition.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase4");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("E-mail address").fill("mhc@uc.pt");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function openEditor(page: Page): Promise<string> {
  await page.getByRole("link", { name: "Projects" }).click();
  await page.getByRole("link", { name: /Processos da Inquisição de Coimbra/ }).click();
  await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
  await page.getByTestId("document-table").getByRole("link", { name: "PT-TT-TSO-IL-28-7926_m0052" }).click();
  await page.getByTestId("page-grid").getByRole("link").first().click();
  await page.getByRole("button", { name: "Open in editor" }).click();
  await expect(page).toHaveURL(/\/transcriptions\//);
  await expect(page.getByTestId("ws-lines").getByRole("listitem").first()).toBeVisible();
  return page.url();
}

const line = (page: Page, n: number) => page.getByLabel(`Line ${n}`, { exact: true });
const saved = (page: Page, rev: number) => expect(page.getByTestId("save-state")).toHaveText(`Saved · revision ${rev}`, { timeout: 15_000 });

test.describe.serial("Transcription workspace", () => {
  let url = "";

  test("open the real HTR output; image and text are synchronised", async ({ page }) => {
    await login(page);
    url = await openEditor(page);
    await saved(page, 1);
    expect(await page.getByTestId("ws-lines").getByRole("listitem").count()).toBeGreaterThan(15);
    // text → image
    await line(page, 3).click();
    const id3 = await line(page, 3).getAttribute("data-line-input");
    await expect(page.locator(`polygon[data-line-id="${id3}"]`)).toHaveClass(/fill-\[#23395b\]\/25/);
    // image → text
    const id5 = await line(page, 5).getAttribute("data-line-input");
    await page.locator(`polygon[data-line-id="${id5}"]`).dispatchEvent("click");
    await expect(line(page, 5)).toBeFocused();
    // zoom
    const z0 = await page.getByTestId("zoom-level").textContent();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(page.getByTestId("zoom-level")).not.toHaveText(z0!);
    await shot(page, "01-workspace");
  });

  test("correct a line with editorial markup; autosave creates revisions; undo/redo", async ({ page }) => {
    await login(page);
    await page.goto(url);
    const l3 = line(page, 3);
    const original = await l3.inputValue();
    await l3.click();
    await l3.press("End");
    await l3.pressSequentially(" dita");
    await l3.press("Shift+ArrowLeft");
    await l3.press("Shift+ArrowLeft");
    await l3.press("Shift+ArrowLeft");
    await l3.press("Shift+ArrowLeft");
    await page.getByRole("toolbar").getByRole("button", { name: /\{\?…\?\}/ }).click();
    await expect(l3).toHaveValue(`${original} {?dita?}`);
    await saved(page, 2);
    await expect(page.getByTestId("ws-lines").getByRole("listitem").nth(2)).toContainText("Corrected");
    // undo → previous text is saved as a new revision (history is never rewritten)
    // step 1 undoes the wrap, step 2 the typing (typing is grouped into one step)
    await page.keyboard.press("Control+z");
    await expect(l3).toHaveValue(`${original} dita`);
    await page.keyboard.press("Control+z");
    await expect(l3).toHaveValue(original);
    await saved(page, 3);
    await page.keyboard.press("Control+Shift+z");
    await page.keyboard.press("Control+Shift+z");
    await expect(l3).toHaveValue(`${original} {?dita?}`);
    await saved(page, 4);
    // invalid markup is flagged and not saved
    await l3.press("End");
    await l3.pressSequentially(" {?x");
    await expect(page.getByRole("alert").filter({ hasText: "unclosed" })).toBeVisible();
    await page.waitForTimeout(2500);
    await expect(page.getByTestId("save-state")).toHaveText("Unsaved changes");
    for (let i = 0; i < 4; i++) await l3.press("Backspace");
    await saved(page, 4);
    // details: original HTR, normalised layer and note
    await expect(page.getByTestId("original-text")).toHaveText(original);
    await page.getByTestId("normalized-input").fill("texto normalizado de teste");
    await page.getByTestId("note-input").fill("Leitura de 'dita' a confirmar no original.");
    await saved(page, 5);
    await shot(page, "02-editing");
  });

  test("abbreviation expansion, views and comparison with the HTR output", async ({ page }) => {
    await login(page);
    await page.goto(url);
    const l4 = line(page, 4);
    await l4.click();
    await l4.press("Home");
    await l4.press("Shift+ArrowRight");
    await page.getByRole("toolbar").getByRole("button", { name: /\{a\|b\}/ }).click();
    await page.keyboard.type("Q");
    await saved(page, 6);
    const v = await l4.inputValue();
    expect(v).toMatch(/^\{.\|Q\}/);
    await page.getByLabel("View").selectOption("expanded");
    await expect(page.getByTestId("ws-lines").getByRole("listitem").nth(3)).toContainText(/^4Q/);
    await page.getByLabel("View").selectOption("compare");
    await expect(page.getByTestId("diff-line").nth(2).locator("span.bg-success-soft")).toHaveCount(1);
    await shot(page, "03-compare");
    await page.getByLabel("View").selectOption("edit");
  });

  test("history: view a past revision read-only and restore a line version", async ({ page }) => {
    await login(page);
    await page.goto(url);
    await page.getByRole("button", { name: "History" }).click();
    const panel = page.getByTestId("revisions-panel");
    await expect(panel.getByRole("listitem")).toHaveCount(6);
    await panel.getByRole("listitem").last().getByRole("button", { name: "View" }).click();
    await expect(page.getByTestId("viewing-banner")).toContainText("Viewing revision 1 of 6");
    await expect(line(page, 3)).toHaveAttribute("readonly", "");
    await shot(page, "04-past-revision");
    await page.getByRole("button", { name: "Back to current" }).click();
    await line(page, 3).click();
    await page.getByRole("button", { name: "Line history" }).click();
    const hist = page.getByTestId("line-history");
    await expect(hist.getByRole("listitem")).toHaveCount(5); // r1 htr, r2, r3, r4, r5 (note/normalised)
    await hist.getByRole("listitem").last().getByRole("button", { name: "Restore" }).click();
    await saved(page, 7);
  });

  test("comments and queries", async ({ page }) => {
    await login(page);
    await page.goto(url);
    await line(page, 6).click();
    await page.getByRole("button", { name: "Comments" }).click();
    await page.getByLabel("Kind").selectOption("query");
    await page.getByLabel("Add comment").fill("Abreviatura no fim da linha: “dto” ou “dito”?");
    await page.getByRole("button", { name: "Add comment" }).click();
    await expect(page.getByTestId("comments-panel")).toContainText("#6");
    await expect(page.getByTestId("comments-panel")).toContainText("“dto” ou “dito”");
    await shot(page, "05-comments");
  });

  test("concurrent edits in two sessions produce an explicit conflict", async ({ browser }) => {
    const a = await browser.newPage();
    const b = await browser.newPage();
    await login(a);
    await login(b);
    await a.goto(url);
    await b.goto(url);
    await saved(a, 7);
    await saved(b, 7);
    await line(a, 8).fill("versão da sessão A");
    await saved(a, 8);
    await line(b, 8).fill("versão da sessão B");
    await expect(b.getByTestId("conflict-banner")).toContainText("versão da sessão A".normalize("NFD"), { timeout: 15_000 });
    await shot(b, "06-conflict");
    await b.getByRole("button", { name: "Use mine" }).click();
    await saved(b, 9);
    await a.reload();
    // the project policy (from the TraPrInq model card) stores text in NFD
    await expect(line(a, 8)).toHaveValue("versão da sessão B".normalize("NFD"));
    await a.close();
    await b.close();
  });

  test("layout adjustment: delete lines, draw a baseline, real re-recognition", async ({ page }) => {
    test.setTimeout(5 * 60_000);
    await login(page);
    await page.goto(url);
    await page.getByRole("button", { name: "Edit layout" }).click();
    const before = Number((await page.getByText(/^\d+ lines$/).textContent())!.split(" ")[0]);
    const polys = page.getByTestId("line-overlay").locator("polygon");
    await polys.first().dispatchEvent("click");
    await page.getByRole("button", { name: "Delete line" }).click();
    await expect(page.getByText(`${before - 1} lines`)).toBeVisible();
    // draw a new baseline across an area of the page
    await page.getByRole("button", { name: "Draw baseline" }).click();
    const box = (await page.getByTestId("image-canvas").boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.2, box.y + box.height * 0.9);
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.9);
    await page.mouse.dblclick(box.x + box.width * 0.5, box.y + box.height * 0.9);
    await expect(page.getByText(`${before} lines`)).toBeVisible();
    await shot(page, "07-layout-edit");
    await page.getByRole("button", { name: "Save layout and recognise" }).click();
    await expect(page.getByText(/New layout saved; recognition job submitted with TraPrInq/)).toBeVisible();
    // The worker computes the missing polygon with Kraken and recognises the new layout.
    await page.getByRole("link", { name: /Processos da Inquisição de Coimbra .* PT-TT-TSO-IL-28-7926_m0052/ }).click();
    await expect(page).toHaveURL(/\/documents\//);
    const jobs = page.getByTestId("job-table");
    await expect(jobs.locator('[data-status="completed"]')).toHaveCount(2, { timeout: 4 * 60_000 });
  });

  test("corrections persist and feed the dashboard", async ({ page }) => {
    await login(page);
    await page.goto(url);
    await expect(line(page, 8)).toHaveValue("versão da sessão B".normalize("NFD"));
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page.getByTestId("dashboard-processing")).toContainText(/Corrected [1-9]/);
  });
});
