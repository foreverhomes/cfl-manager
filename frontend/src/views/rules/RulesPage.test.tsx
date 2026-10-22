// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitForElementToBeRemoved, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import type { LeagueRule, LeagueRules, RuleChange, RuleEdit } from "../../api/generated";
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
    rule("contracts.buyout_share", "Share of the salary a buyout costs", "share", "Buyouts and Releases", 0.29),
    rule("draft.rounds", "Entry draft rounds", "count", "Entry Draft", 5),
  ],
};
const admin: LeagueRules = { ...page, can_edit: true };

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
// Answers each rule change with `answer`, and keeps what the page sent.
const recordChanges = (answer = admin) => {
  const sent: object[] = [];
  server.use(
    http.put<{ key: string }, RuleEdit>("*/rules/:key", async ({ params, request }) => {
      sent.push({ key: params.key, ...(await request.json()) });
      return HttpResponse.json(answer);
    }),
  );
  return sent;
};
const openForm = async (label: string) => {
  fireEvent.click(
    within(await screen.findByRole("row", { name: new RegExp(label) })).getByRole("button", { name: "Change" }),
  );
  return within(await screen.findByRole("dialog"));
};

it("shows each rule's value in its own terms, under its area", async () => {
  showPage(() => HttpResponse.json(page));
  expect(await screen.findByRole("heading", { name: "Contracts and the cap" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Entry draft" })).toBeTruthy();
  expect(row("Salary cap").getByText("Fantrax's")).toBeTruthy();
  expect(row("UFA age").getByText("July 1")).toBeTruthy();
  expect(row("released for free").getByText("$3")).toBeTruthy();
  expect(row("buyout costs").getByText("29%")).toBeTruthy();
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

it("gives a GM no way to change a rule", async () => {
  showPage(() => HttpResponse.json(page));
  await screen.findByText("Contract-year bank");
  expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
});

it("opens a rule's form from its Change button", async () => {
  showPage(() => HttpResponse.json(admin));
  await openForm("Contract-year bank");
  expect(screen.getByRole("dialog", { name: "Contract-year bank" })).toBeTruthy();
});

it("saves a change from a season with its reason, then shows the rules as they are now", async () => {
  const saved = {
    ...admin,
    rules: [rule("contracts.year_bank", "Contract-year bank", "count", "Contracts", 50, { value: 60 })],
  };
  const sent = recordChanges(saved);
  server.use(
    http.get("*/rules", () => HttpResponse.json(admin), { once: true }),
    http.get("*/rules", () => HttpResponse.json(saved)),
  );
  renderView(<RulesPage />);
  const form = await openForm("Contract-year bank");
  fireEvent.change(form.getByLabelText("Value"), { target: { value: "60" } });
  fireEvent.click(form.getByLabelText("Starts in"));
  // jsdom has no layout, so Mantine's Popover takes the box to be off screen and hides the list.
  fireEvent.click(await screen.findByRole("option", { name: "2027/28", hidden: true }));
  fireEvent.change(form.getByLabelText("Why"), { target: { value: "2027 league vote" } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toEqual([{ key: "contracts.year_bank", value: 60, from_year: 2027, note: "2027 league vote" }]);
  expect(await screen.findByText("60")).toBeTruthy();
});

it.each([
  ["money", "released for free", "$4.5", 4.5],
  ["share", "buyout costs", "40%", 0.4],
  ["month_day", "UFA age is measured on", "06-15", "06-15"],
])("sends a %s value as the API takes it", async (_kind, label, typed, value) => {
  const sent = recordChanges();
  showPage(() => HttpResponse.json(admin));
  const form = await openForm(label);
  fireEvent.change(form.getByLabelText("Value"), { target: { value: typed } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toMatchObject([{ value }]);
});

it("puts a rule's default back", async () => {
  const sent = recordChanges();
  showPage(() => HttpResponse.json(admin));
  const form = await openForm("Salary cap");
  fireEvent.click(form.getByLabelText("Use the default: Fantrax's"));
  expect(form.getByLabelText("Value")).toHaveProperty("disabled", true);
  fireEvent.click(form.getByRole("button", { name: "Save" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toMatchObject([{ key: "contracts.salary_cap", value: null }]);
});

it("shows why the API refused a change, and keeps the form open", async () => {
  const refusal = "From 2026: a round 5 pick in the 2026 draft changed hands";
  server.use(http.put("*/rules/:key", () => HttpResponse.json({ detail: refusal }, { status: 400 })));
  showPage(() => HttpResponse.json(admin));
  const form = await openForm("Entry draft rounds");
  fireEvent.change(form.getByLabelText("Value"), { target: { value: "4" } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));

  expect((await form.findByRole("alert")).textContent).toBe(refusal);
  expect(form.getByLabelText("Value")).toHaveProperty("value", "4");
});
