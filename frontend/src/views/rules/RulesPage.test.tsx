// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import type { LeagueRule, LeagueRules, RuleChange } from "../../api/generated";
import { queryClient } from "../../queries/client";
import { renderView } from "../test-utils";
import { RulesPage } from "./RulesPage";

const change = (from_year: number, value: number | null, note: string | null): RuleChange => ({
  from_year,
  value,
  note,
  changed_by: "Boss",
  changed_at: "2026-11-02T12:00:00+00:00",
});

const rule = (
  key: string,
  label: string,
  kind: string,
  cite: string,
  value: LeagueRule["value"],
  extra: Partial<LeagueRule> = {},
): LeagueRule => ({
  key,
  label,
  kind,
  cite,
  default: value,
  value,
  changes: [],
  ...extra,
});

const page: LeagueRules = {
  year: 2026,
  league_year: 2026,
  can_edit: false,
  rules: [
    rule("contracts.salary_cap", "Salary cap", "money", "Salaries", null),
    rule("contracts.year_bank", "Contract-year bank", "count", "Contracts", 50, {
      value: 55,
      changes: [change(2028, 60, "2027 league vote"), change(2026, 55, null)],
    }),
    rule("contracts.ufa_day", "The day the UFA age is measured on", "month_day", "UFA", "07-01"),
    rule("contracts.free_release_max", "Highest salary released for free", "money", "Buyouts and Releases", 3),
    rule("contracts.buyout_share", "Share of the salary a buyout costs", "share", "Buyouts and Releases", 0.5),
    rule("draft.rounds", "Entry draft rounds", "count", "Entry Draft", 5),
  ],
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  queryClient.clear();
});
afterAll(() => server.close());

const showPage = (respond: () => Response) => {
  server.use(http.get("*/rules", respond));
  renderView(<RulesPage />);
};
const row = (label: string) => within(screen.getByRole("row", { name: new RegExp(label) }));

it("shows each rule's value in its own terms, under its area", async () => {
  showPage(() => HttpResponse.json(page));
  expect(await screen.findByRole("heading", { name: "Contracts and the cap" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Entry draft" })).toBeTruthy();
  expect(row("Salary cap").getByText("Fantrax's")).toBeTruthy();
  expect(row("UFA age").getByText("July 1")).toBeTruthy();
  expect(row("released for free").getByText("$3")).toBeTruthy();
  expect(row("buyout costs").getByText("50%")).toBeTruthy();
  expect(row("Entry draft rounds").getByText("5")).toBeTruthy();
});

it("shows a changed rule's default and every change, newest first", async () => {
  showPage(() => HttpResponse.json(page));
  await screen.findByText("Contract-year bank");
  const bank = row("Contract-year bank");
  expect(bank.getByText("55")).toBeTruthy();
  expect(bank.getByText("Default 50")).toBeTruthy();
  expect(bank.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "60 from 2028/29: 2027 league vote · Boss, Nov 2, 2026",
    "55 from 2026/27 · Boss, Nov 2, 2026",
  ]);
  expect(row("Entry draft rounds").queryByText(/Default/)).toBeNull();
});

it("links each rule to its section of the league's written rules", async () => {
  showPage(() => HttpResponse.json(page));
  const link = (await screen.findAllByRole("link", { name: "Buyouts and Releases" }))[0];
  expect(link.getAttribute("href")).toBe(
    "https://github.com/foreverhomes/cfl-manager/blob/main/docs/rules.md#buyouts-and-releases",
  );
});

it("says why when the rules can't load", async () => {
  showPage(() => HttpResponse.json({ detail: "League not synced yet: POST /sync" }, { status: 409 }));
  expect(await screen.findByText("Couldn't load the rules. League not synced yet: POST /sync")).toBeTruthy();
});
