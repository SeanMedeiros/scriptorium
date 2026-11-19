import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Phase 7 (production readiness) on the real stack started by scripts/e2e.sh:
// HTTP security headers and CSP, self-hosted API documentation (no third-party requests),
// the institutional storage policy managed in the UI, and — when the stack runs with a real
// ClamAV daemon (E2E_MALWARE_SCANNER=clamd) — an infected upload refused by the scanner.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase7");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
const EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("E-mail address").fill("mhc@uc.pt");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test.describe.serial("Production readiness", () => {
  test("web and API responses carry security headers; API docs are self-hosted", async ({ page, request }) => {
    const web = await request.get("/login");
    const h = web.headers();
    expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(h["content-security-policy"]).toContain("connect-src 'self'");
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["x-content-type-options"]).toBe("nosniff");
    const api = await request.get("/api/health/ready");
    expect(api.headers()["content-security-policy"]).toContain("default-src 'none'");
    const ready = await api.json();
    expect(ready.status).toBe("ok");
    if (process.env.E2E_MALWARE_SCANNER === "clamd") expect(ready.checks.malware_scanner.version).toMatch(/^ClamAV/);

    const external: string[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (!["127.0.0.1", "localhost"].includes(u.hostname)) external.push(r.url());
    });
    await page.goto("/api/docs");
    await expect(page.getByText("SCRIPTORIUM API").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".opblock-summary-path").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    // The explorer renders tag groups progressively; the contract itself is checked on the spec.
    const spec = await (await request.get("/api/openapi.json")).json();
    expect(Object.keys(spec.paths)).toEqual(expect.arrayContaining([
      "/api/v1/institutions/{institution_id}/storage-policy", "/api/v1/admin/retention", "/api/v1/admin/users"]));
    await shot(page, "01-api-docs-self-hosted");
    expect(external).toEqual([]);
  });

  test("institution administrator sets the storage policy", async ({ page }) => {
    await login(page);
    await page.getByRole("link", { name: "Institutions" }).click();
    const card = page.locator("section", { has: page.getByRole("heading", { name: "Storage policy" }) }).first();
    await expect(card.getByTestId("storage-usage")).toContainText(/documents across 1 projects/);
    await card.getByLabel(/Keep export archives/).fill("14");
    await card.getByLabel("TIFF").uncheck();
    await card.getByLabel(/Data-hosting statement/).fill("Documents are stored in the University of Coimbra data centre.");
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card.getByText("Storage policy saved.")).toBeVisible();
    await expect(card.getByTestId("storage-effective")).toContainText("PDF, JPEG, PNG");
    await expect(card.getByTestId("storage-effective")).toContainText("14 days");
    await expect(card.getByText("University of Coimbra data centre")).toBeVisible();
    await shot(page, "02-storage-policy");
  });

  test("an administrator invites a researcher who sets their own password", async ({ page }) => {
    const outbox = process.env.E2E_OUTBOX!;
    const before = new Set(fs.existsSync(outbox) ? fs.readdirSync(outbox) : []);
    await login(page);
    await page.getByRole("link", { name: "Institutions" }).click();
    await page.getByRole("button", { name: "Invite user" }).first().click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Full name").fill("Ana Paleógrafa");
    await dlg.getByLabel("E-mail address").fill("ana@uc.pt");
    await dlg.getByRole("button", { name: "Send invitation" }).click();
    await expect(page.getByText("Invitation sent to ana@uc.pt.")).toBeVisible();
    await expect(page.getByRole("cell", { name: "ana@uc.pt" })).toBeVisible();
    const mail = fs.readdirSync(outbox).find((f) => !before.has(f))!;
    const raw = fs.readFileSync(path.join(outbox, mail), "utf8").replace(/=\r?\n/g, "").replace(/=3D/g, "=");
    const link = raw.match(/http:\/\/127\.0\.0\.1:3001\/reset-password\?token=[\w-]+/)![0];
    await page.context().clearCookies();
    await page.goto(link);
    await page.getByLabel("New password").fill("Paleografia-Coimbra-1590");
    await page.getByRole("button", { name: "Set new password" }).click();
    await expect(page.getByText("Your password has been changed")).toBeVisible();
    await page.goto("/login");
    await page.getByLabel("E-mail address").fill("ana@uc.pt");
    await page.getByLabel("Password").fill("Paleografia-Coimbra-1590");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("an infected upload is refused by the real malware scanner", async ({ page }) => {
    test.skip(process.env.E2E_MALWARE_SCANNER !== "clamd", "stack started without clamd");
    const file = path.join(path.resolve(__dirname, "../test-results"), "carta-suspeita.pdf");
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% ${EICAR}\n%%EOF\n`);
    await login(page);
    await page.getByRole("link", { name: "Projects" }).click();
    await page.getByRole("link", { name: /Processos da Inquisição de Coimbra/ }).click();
    await page.getByRole("link", { name: "Tribunal do Santo Ofício — Coimbra" }).click();
    const table = page.getByTestId("document-table");
    await expect(table.getByText("PT-TT-TSO-IL-28-7926_m0052", { exact: true })).toBeVisible();
    const before = await table.getByRole("row").count();
    await page.getByRole("button", { name: "Upload documents" }).click();
    await page.getByTestId("file-input").setInputFiles([file]);
    await page.getByRole("button", { name: "Upload documents" }).last().click();
    await expect(page.getByRole("dialog").getByText("'carta-suspeita.pdf' was rejected by the malware scanner.")).toBeVisible();
    await shot(page, "03-malware-refused");
    await page.keyboard.press("Escape");
    await expect(table.getByRole("row")).toHaveCount(before);
    await expect(table.getByText("carta-suspeita")).toHaveCount(0); // nothing was stored
  });
});
