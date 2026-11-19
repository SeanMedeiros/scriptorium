import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { I18nProvider, useI18n } from "@/i18n/I18nProvider";
import { format, LOCALES, MESSAGES } from "@/i18n/messages";

describe("message catalogues", () => {
  const reference = Object.keys(MESSAGES.en).sort();
  it.each(LOCALES)("locale %s defines every key and no empty strings", (l) => {
    expect(Object.keys(MESSAGES[l]).sort()).toEqual(reference);
    for (const [k, v] of Object.entries(MESSAGES[l])) expect(v.trim(), k).not.toBe("");
  });
  it.each(LOCALES)("locale %s keeps the same placeholders as English", (l) => {
    for (const [k, v] of Object.entries(MESSAGES.en)) {
      const vars = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
      expect(vars(MESSAGES[l][k as keyof typeof MESSAGES.en]), k).toEqual(vars(v));
    }
  });
  it("interpolates variables", () => {
    expect(format("{n} collections", { n: 3 })).toBe("3 collections");
    expect(format("keep {unknown}", {})).toBe("keep {unknown}");
  });
});

function Probe() {
  const { t } = useI18n();
  return <h1>{t("nav.projects")}</h1>;
}

describe("I18nProvider + LocaleSwitcher", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to English and switches language, persisting the choice", async () => {
    render(
      <I18nProvider>
        <Probe />
        <LocaleSwitcher />
      </I18nProvider>,
    );
    expect(screen.getByRole("heading")).toHaveTextContent("Projects");
    await userEvent.click(screen.getByRole("button", { name: "pt" }));
    expect(screen.getByRole("heading")).toHaveTextContent("Projetos");
    expect(document.documentElement.lang).toBe("pt");
    expect(localStorage.getItem("scriptorium.locale")).toBe("pt");
    await userEvent.click(screen.getByRole("button", { name: "fr" }));
    expect(screen.getByRole("heading")).toHaveTextContent("Projets");
    expect(screen.getByRole("button", { name: "fr" })).toHaveAttribute("aria-pressed", "true");
  });

  it("restores a saved locale", async () => {
    localStorage.setItem("scriptorium.locale", "fr");
    await act(async () => {
      render(
        <I18nProvider>
          <Probe />
        </I18nProvider>,
      );
    });
    expect(screen.getByRole("heading")).toHaveTextContent("Projets");
  });
});
