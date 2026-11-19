import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Real browser, real Next.js server, real FastAPI, real PostgreSQL. No mocks.
const PW = "Correct-Horse-Battery-9";
const SHOTS = path.resolve(__dirname, "../../docs/evidence/phase1");
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });

async function register(page: Page, name: string, email: string) {
  await page.goto("/register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("E-mail address").fill(email);
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test.describe.serial("Phase 1 — foundation", () => {
  let projectUrl = "";

  test("first account becomes platform admin and sees an honest empty dashboard", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await shot(page, "01-login");
    await register(page, "Maria Helena Coelho", "mhc@uc.pt");
    const stats = page.getByTestId("dashboard-stats");
    await expect(stats).toContainText("Research projects");
    await expect(stats).toContainText("0");
    await expect(page.getByText("Start your first research project")).toBeVisible();
    // No ground truth yet: the quality block states it instead of showing figures.
    await expect(page.getByTestId("dashboard-quality")).toContainText("Error rates appear only after models are scored");
    await expect(page.getByRole("link", { name: "Users" })).toBeVisible(); // admin-only entry
    await shot(page, "02-dashboard-empty");
  });

  test("admin creates an institution, a project and a collection", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("E-mail address").fill("mhc@uc.pt");
    await page.getByLabel("Password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);

    await page.getByRole("link", { name: "Institutions" }).click();
    await page.getByRole("button", { name: "New institution" }).click();
    await page.getByLabel("Name").fill("Arquivo da Universidade de Coimbra");
    await page.getByLabel("Identifier (slug)").fill("auc");
    await page.getByLabel(/Country/).fill("PT");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("heading", { name: "Arquivo da Universidade de Coimbra" })).toBeVisible();
    // The platform admin adds themself as institution administrator.
    await page.getByLabel("E-mail address").fill("mhc@uc.pt");
    await page.getByLabel("Role").selectOption("institution_admin");
    await page.getByRole("button", { name: "Add member" }).click();
    await expect(page.getByRole("cell", { name: "mhc@uc.pt" })).toBeVisible();
    await shot(page, "03-institution");

    await page.getByRole("link", { name: "Projects" }).click();
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByLabel("Title").fill("Processos da Inquisição de Coimbra");
    await page.getByLabel("Institution").selectOption({ label: "Arquivo da Universidade de Coimbra" });
    await page.getByLabel(/Description/).fill("Transcrição de processos inquisitoriais, séculos XVI–XVIII.");
    await page.getByLabel(/Languages/).fill("por, lat");
    await page.getByLabel("From (year)").fill("1541");
    await page.getByLabel("To (year)").fill("1821");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("heading", { name: "Processos da Inquisição de Coimbra" })).toBeVisible();
    projectUrl = page.url();

    await page.getByRole("button", { name: "New collection" }).click();
    await page.getByLabel("Title").fill("Tribunal do Santo Ofício — Coimbra");
    await page.getByLabel("Archival reference").fill("PT/TT/TSO-IC");
    await page.getByLabel(/Holding institution/).fill("Arquivo Nacional Torre do Tombo");
    await page.getByLabel("dc:coverage").fill("Coimbra");
    await page.getByLabel("dc:date").fill("1541/1821");
    await page.getByRole("button", { name: "Create" }).click();
    const table = page.getByTestId("collection-table");
    await expect(table).toContainText("PT/TT/TSO-IC");
    await expect(table).toContainText("dc:coverage");
    await shot(page, "04-project-collections");

    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.getByText("collection.create · Tribunal do Santo Ofício — Coimbra")).toBeVisible();
    await expect(page.getByText("project.create · Processos da Inquisição de Coimbra")).toBeVisible();

    await page.getByRole("link", { name: "Dashboard" }).click();
    const stats = page.getByTestId("dashboard-stats");
    await expect(stats.locator("div", { hasText: /^Research projects1$/ })).toBeVisible();
    await expect(stats.locator("div", { hasText: /^Collections1$/ })).toBeVisible();
    await shot(page, "05-dashboard-populated");
  });

  test("session survives a page reload (HttpOnly refresh cookie) and logout ends it", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("E-mail address").fill("mhc@uc.pt");
    await page.getByLabel("Password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    const cookies = await page.context().cookies();
    const refresh = cookies.find((c) => c.name === "scriptorium_refresh");
    expect(refresh?.httpOnly).toBe(true);
    expect(refresh?.path).toBe("/api/v1/auth");
    await page.reload();
    await expect(page.getByTestId("dashboard-stats")).toBeVisible();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("interface localisation (pt, fr)", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "pt" }).click();
    await expect(page.getByRole("heading", { name: "Entrar no Scriptorium" })).toBeVisible();
    await page.getByLabel("Endereço de e-mail").fill("mhc@uc.pt");
    await page.getByLabel("Palavra-passe").fill(PW);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    // The user's stored preference (en) is applied at sign-in; switch and persist pt.
    await expect(page.getByRole("link", { name: "Projects" })).toBeVisible();
    await page.getByRole("button", { name: "pt" }).click();
    await expect(page.getByRole("link", { name: "Projetos" })).toBeVisible();
    await shot(page, "06-dashboard-pt");
    await page.getByRole("button", { name: "fr" }).click();
    await expect(page.getByRole("link", { name: "Projets" })).toBeVisible();
    await page.getByRole("button", { name: "en" }).click();
  });

  test("a second researcher cannot see another institution's project", async ({ page }) => {
    await register(page, "Jean Dupont", "jean@ens.fr");
    await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
    await page.goto(projectUrl);
    await expect(page.getByText("Not found, or you do not have access.")).toBeVisible();
    await page.getByRole("link", { name: "Projects" }).click();
    await expect(page.getByText("No projects yet.")).toBeVisible();
    await shot(page, "07-isolation");
  });

  test("password reset through the e-mail outbox", async ({ page }) => {
    const outbox = process.env.E2E_OUTBOX!;
    const before = new Set(fs.existsSync(outbox) ? fs.readdirSync(outbox) : []);
    await page.goto("/forgot-password");
    await page.getByLabel("E-mail address").fill("jean@ens.fr");
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByText(/a reset link has been sent/)).toBeVisible();
    const mail = fs.readdirSync(outbox).find((f) => !before.has(f))!;
    const raw = fs.readFileSync(path.join(outbox, mail), "utf8").replace(/=\r?\n/g, "").replace(/=3D/g, "=");
    const link = raw.match(/http:\/\/127\.0\.0\.1:3001\/reset-password\?token=[\w-]+/)![0];
    await page.goto(link);
    await page.getByLabel("New password").fill("Une-nouvelle-phrase-secrete-1");
    await page.getByRole("button", { name: "Set new password" }).click();
    await expect(page.getByText("Your password has been changed")).toBeVisible();
    await page.goto("/login");
    await page.getByLabel("E-mail address").fill("jean@ens.fr");
    await page.getByLabel("Password").fill("Une-nouvelle-phrase-secrete-1");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("audit log page shows the append-only trail", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("E-mail address").fill("mhc@uc.pt");
    await page.getByLabel("Password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("link", { name: "Audit log" }).click();
    for (const a of ["institution.create", "project.create", "collection.create", "auth.login", "auth.password_reset"]) {
      await expect(page.getByRole("cell", { name: a, exact: true }).first()).toBeVisible();
    }
    await shot(page, "08-audit-log");
  });
});
