// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import type { ContractEnd, ExpiringContracts, ExpiringPlayer, Team } from "../../api/generated";
import { onOpenPlayer } from "../../bridge";
import { queryClient } from "../../queries/client";
import { renderView } from "../test-utils";
import { OffseasonPage } from "./OffseasonPage";

const player = (
  fantrax_id: string,
  name: string,
  extra: Partial<ExpiringPlayer> = {},
  end: Partial<ContractEnd> = {},
): ExpiringPlayer => ({
  fantrax_id,
  name,
  nhl_team: "TOR",
  position: "C",
  eligible: ["C", "F"],
  fantasy_team_id: "t1",
  fantasy_team: "Lindy Ruffryders",
  roster_status: "ACTIVE",
  gp_this_season: 8,
  gp_last_season: 79,
  value: 4.53,
  trade_rank: 29,
  contract_type: "STANDARD",
  contract_years: 1,
  final_salary: 6,
  contract_end: { after_season: "2026/27", year: 2027, status: "RFA", age_at_expiry: 24, reason: null, ...end },
  ...extra,
});

const whale = { fantasy_team_id: "t2", fantasy_team: "Whale Emoji" };
const list: ExpiringContracts = {
  year: 2027,
  after_season: "2026/27",
  ufa_age: 27,
  offseasons: [
    { year: 2027, after_season: "2026/27" },
    { year: 2028, after_season: "2027/28" },
  ],
  players: [
    player("a1", "Alex Carter"),
    player("a2", "Blake Young", { ...whale, position: "LW", eligible: ["LW", "F"], final_salary: 14 }),
    player("a3", "Sam Brooks", { ...whale, position: "D", eligible: ["D"], final_salary: 9 }),
    player("a4", "Jordan Lee", { final_salary: 4, contract_type: "ELC" }, { status: "UFA", age_at_expiry: 31 }),
    player("a5", "Riley Quinn", { position: "G", eligible: ["G"], final_salary: 10 }, { status: "UFA" }),
  ],
  no_contract: [{ fantrax_id: "n1", name: "Casey Moore", ...whale }],
};
const later: ExpiringContracts = {
  ...list,
  year: 2028,
  after_season: "2027/28",
  players: [player("a6", "Drew Park", { contract_years: 2 })],
};
const team = (id: string, name: string): Team => ({
  id,
  name,
  short_name: null,
  rank: null,
  record: null,
  games_back: null,
  win_pct: null,
  is_me: 0,
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  queryClient.clear();
});
afterAll(() => server.close());

// Answers /offseason with `answer` for the year asked (null: none given), and keeps each year asked for.
const showPage = (answer: (year: string | null) => Response = () => HttpResponse.json(list)) => {
  const asked: (string | null)[] = [];
  server.use(
    http.get("*/offseason", ({ request }) => {
      const year = new URL(request.url).searchParams.get("year");
      asked.push(year);
      return answer(year);
    }),
    http.get("*/teams", () => HttpResponse.json([team("t1", "Lindy Ruffryders"), team("t2", "Whale Emoji")])),
  );
  renderView(<OffseasonPage />);
  return asked;
};
const names = () =>
  within(screen.getByRole("table"))
    .getAllByTitle("Edit contract")
    .map((link) => link.textContent);
const pick = async (label: string, option: string) => {
  fireEvent.click(await screen.findByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: option, hidden: true }));
};

it("lists the next offseason's contracts by status, then position, the biggest salary first", async () => {
  const asked = showPage();
  expect(await screen.findByRole("heading", { name: "All contracts ending after 2026/27" })).toBeTruthy();
  expect(names()).toEqual(["Blake Young", "Alex Carter", "Sam Brooks", "Jordan Lee", "Riley Quinn"]);
  expect(screen.getByText("3 players · $29 salary")).toBeTruthy();
  expect(screen.getByText(/players 27 or older become UFAs/)).toBeTruthy();
  expect(asked).toEqual([null]);
});

it("counts each status and its final-season salaries", async () => {
  showPage();
  expect(await screen.findByText("$29 in 2026/27 salaries")).toBeTruthy();
  expect(screen.getByText("$14 in 2026/27 salaries")).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "Unknown" })).toBeNull();
});

it("shows the players with no birth date apart", async () => {
  const nodob = player("a7", "Pat Ellis", {}, { status: null, age_at_expiry: null });
  showPage(() => HttpResponse.json({ ...list, players: [...list.players, nodob] }));
  expect(await screen.findByText("Add birth dates to place them")).toBeTruthy();
  expect(screen.getByRole("radio", { name: "Unknown" })).toBeTruthy();
  expect(names().at(-1)).toBe("Pat Ellis");
});

it("shows one status", async () => {
  showPage();
  fireEvent.click(await screen.findByRole("radio", { name: "UFAs" }));
  expect(names()).toEqual(["Jordan Lee", "Riley Quinn"]);
});

it("sorts by a column, and again the other way", async () => {
  showPage();
  fireEvent.click(await screen.findByRole("button", { name: "Player" }));
  expect(names().slice(0, 2)).toEqual(["Alex Carter", "Blake Young"]);
  fireEvent.click(screen.getByRole("button", { name: "Player ↑" }));
  expect(names().slice(0, 2)).toEqual(["Blake Young", "Alex Carter"]);
  expect(screen.getByRole("columnheader", { name: "Player ↓" }).getAttribute("aria-sort")).toBe("descending");
});

it("shows one team's contracts, without the team column", async () => {
  showPage();
  await pick("Team", "Whale Emoji");
  expect(screen.getByRole("heading", { name: "Whale Emoji’s contracts ending after 2026/27" })).toBeTruthy();
  expect(names()).toEqual(["Blake Young", "Sam Brooks"]);
  expect(screen.queryByRole("columnheader", { name: "Team" })).toBeNull();
});

it("loads a later offseason, with the seasons each contract has left", async () => {
  const asked = showPage((year) => HttpResponse.json(year === "2028" ? later : list));
  await pick("Offseason", "2028 (after 2027/28)");
  expect(await screen.findByRole("columnheader", { name: "Yrs left" })).toBeTruthy();
  expect(names()).toEqual(["Drew Park"]);
  expect(asked).toEqual([null, "2028"]);
});

it("goes back to the next offseason when the one picked is no longer offered", async () => {
  const asked = showPage((year) =>
    year === "2028"
      ? HttpResponse.json({ detail: "year must be one of: 2029" }, { status: 400 })
      : HttpResponse.json(list),
  );
  await pick("Offseason", "2028 (after 2027/28)");
  await waitFor(() => expect(asked).toEqual([null, "2028", null]));
  expect((await screen.findByRole<HTMLInputElement>("combobox", { name: "Offseason" })).value).toBe(
    "2027 (after 2026/27)",
  );
});

it("opens a player's contract in the legacy dialog", async () => {
  const dialog = vi.fn();
  onOpenPlayer(dialog);
  showPage();
  fireEvent.click(await screen.findByRole("button", { name: "Alex Carter" }));
  expect(dialog).toHaveBeenCalledWith("a1");
});

it("lists the rostered players with no contract, to open theirs", async () => {
  const dialog = vi.fn();
  onOpenPlayer(dialog);
  showPage();
  fireEvent.click(await screen.findByRole("button", { name: /1 rostered player has no contract on file/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Casey Moore" }));
  expect(dialog).toHaveBeenCalledWith("n1");
});

it("says when no contracts end in the offseason", async () => {
  showPage(() => HttpResponse.json({ ...list, players: [] }));
  expect(await screen.findByText("No contracts on file end after 2026/27.")).toBeTruthy();
});

it("says why the list didn't load", async () => {
  showPage(() => HttpResponse.json({ detail: "Database is down" }, { status: 500 }));
  expect(await screen.findByText("Couldn't load the expiring contracts. Database is down")).toBeTruthy();
});
