// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitForElementToBeRemoved, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import type { FreeAgent, Me, NewUfaBid, Team, UfaBid, UfaBidPreview, UfaBids } from "../../api/generated";
import { queryClient } from "../../queries/client";
import { renderView } from "../test-utils";
import { UfaBidsPage } from "./UfaBidsPage";

const bid = (player_name: string, team_name: string, salary: number, state: string, extra: Partial<UfaBid> = {}) =>
  ({
    id: `${player_name}-${state}`,
    player_id: player_name,
    player_name,
    position: "C",
    nhl_team: "BOS",
    team_id: team_name === "One" ? "t1" : "t2",
    team_name,
    salary,
    state,
    deadline: "2026-11-03T07:30:00+00:00",
    created_by: "Gm",
    created_at: "2026-11-02T07:30:00+00:00",
    on_team_id: null,
    ...extra,
  }) satisfies UfaBid;

const board: UfaBids = {
  now: "2026-11-02T12:00:00+00:00",
  team_id: "t1",
  min_bid: 1,
  min_raise: 1,
  can_withdraw: false,
  bids: [
    bid("Brad Marchand", "Two", 4, "pending"),
    bid("David Pastrnak", "One", 12, "pending", { deadline: "2026-11-02T12:40:00+00:00" }),
    bid("Charlie McAvoy", "One", 6, "won", { on_team_id: "t2" }),
    bid("Elias Lindholm", "Two", 3, "won"),
    bid("Pavel Zacha", "One", 2, "signed", { on_team_id: "t1" }),
    bid("Hampus Lindholm", "One", 5, "outbid"),
  ],
  unbid_pickups: [{ player_id: "f9", player_name: "Morgan Geekie", team_id: "t2", team_name: "Two" }],
};
const gm: Me = { email: "gm@example.com", name: "Gm", team_id: "t1", is_admin: false, status: "active", auth: true };
const boss: Me = { ...gm, email: "boss@example.com", name: "Boss", team_id: null, is_admin: true };
const zacha: FreeAgent = { player_id: "f4", name: "Pavel Zacha", position: "C", nhl_team: "BOS" };
const fine: UfaBidPreview = { min_salary: 1, cap_space: 209, reasons: [] };

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  queryClient.clear();
});
afterAll(() => server.close());

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

const showPage = (user = gm, answer: UfaBids = user.is_admin ? { ...board, team_id: null } : board) => {
  server.use(
    http.get("*/ufa-bids", () => HttpResponse.json(answer)),
    http.get("*/auth/me", () => HttpResponse.json(user)),
    http.get("*/teams", () => HttpResponse.json([team("t1", "One"), team("t2", "Two")])),
  );
  renderView(<UfaBidsPage />);
};
const section = async (title: string) =>
  within((await screen.findByRole("heading", { name: title })).closest("section") as HTMLElement);
const row = (where: ReturnType<typeof within>, name: string) =>
  within(where.getByRole("row", { name: new RegExp(name) }));
// Answers each bid with `answer`, and keeps what the page sent.
const recordBids = (answer: () => Response = () => HttpResponse.json(board)) => {
  const sent: NewUfaBid[] = [];
  server.use(
    http.post<never, NewUfaBid>("*/ufa-bids", async ({ request }) => {
      sent.push(await request.json());
      return answer();
    }),
  );
  return sent;
};

it("shows each open bid with its team and the time it has left", async () => {
  showPage();
  const open = await section("Open bids");
  expect(row(open, "Brad Marchand").getByText("$4")).toBeTruthy();
  expect(row(open, "Brad Marchand").getByText("Two")).toBeTruthy();
  expect(row(open, "Brad Marchand").getByText("19 h 30 min")).toBeTruthy();
  expect(row(open, "David Pastrnak").getByText("40 min")).toBeTruthy();
});

it("tells the winner of each bid what's left to do in Fantrax", async () => {
  showPage();
  const won = await section("Won: add in Fantrax");
  expect(row(won, "Elias Lindholm").getByText("Waiting for the add in Fantrax")).toBeTruthy();
  expect(row(won, "Charlie McAvoy").getByText("Another team has him in Fantrax")).toBeTruthy();
  const done = await section("Earlier this season");
  expect(row(done, "Pavel Zacha").getByText("Signed")).toBeTruthy();
  expect(row(done, "Hampus Lindholm").getByText("Outbid")).toBeTruthy();
});

it("lets a GM raise other teams' bids, and gives them no way to withdraw or void", async () => {
  showPage();
  const open = await section("Open bids");
  expect(row(open, "Brad Marchand").getByRole("button", { name: "Raise" })).toBeTruthy();
  expect(row(open, "David Pastrnak").queryByRole("button", { name: "Raise" })).toBeNull(); // their own
  expect(screen.queryByRole("button", { name: /Withdraw|Void/ })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Pickups with no winning bid" })).toBeNull();
});

it("finds a free agent by name, previews the bid, then shows the new board", async () => {
  const sent = recordBids();
  server.use(
    http.get("*/free-agents", () => HttpResponse.json([zacha])),
    http.get("*/ufa-bids/preview", () => HttpResponse.json(fine)),
  );
  showPage();
  fireEvent.click(await screen.findByRole("button", { name: "New bid" }));
  const form = within(await screen.findByRole("dialog"));
  const search = form.getByLabelText("Player");
  search.focus(); // Select takes typing into a box without focus to be the browser's autofill
  fireEvent.change(search, { target: { value: "zach" } });
  // jsdom has no layout, so Mantine's Popover takes the box to be off screen and hides the list.
  fireEvent.click(await screen.findByRole("option", { name: "Pavel Zacha", hidden: true }));
  fireEvent.change(form.getByLabelText("Bid"), { target: { value: "3" } });
  expect(await form.findByText("Cap space after this bid: $209")).toBeTruthy();
  fireEvent.click(form.getByRole("button", { name: "Bid" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toEqual([{ player_id: "f4", salary: 3, team_id: "t1", force_reason: null }]);
});

it("starts a raise at the lowest bid that beats the high bid", async () => {
  const sent = recordBids();
  server.use(http.get("*/ufa-bids/preview", () => HttpResponse.json({ ...fine, min_salary: 5 })));
  showPage();
  fireEvent.click(row(await section("Open bids"), "Brad Marchand").getByRole("button", { name: "Raise" }));
  const form = within(await screen.findByRole("dialog", { name: "Bid on Brad Marchand" }));
  expect(form.getByLabelText("Bid")).toHaveProperty("value", "$5");
  await form.findByText("Cap space after this bid: $209");
  fireEvent.click(form.getByRole("button", { name: "Bid" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toEqual([{ player_id: "Brad Marchand", salary: 5, team_id: "t1", force_reason: null }]);
});

it("shows why the rules refuse a bid, and gives a GM no way to make it", async () => {
  const why = "One has $2 of cap space left for a $5 bid";
  server.use(http.get("*/ufa-bids/preview", () => HttpResponse.json({ ...fine, cap_space: -3, reasons: [why] })));
  showPage();
  fireEvent.click(row(await section("Open bids"), "Brad Marchand").getByRole("button", { name: "Raise" }));
  const form = within(await screen.findByRole("dialog"));

  expect(await form.findByText(why)).toBeTruthy();
  expect(form.getByRole("button", { name: "Bid" })).toHaveProperty("disabled", true);
  expect(form.queryByLabelText("Reason to allow it")).toBeNull();
});

it("lets the admin allow a bid the rules refuse, with a reason", async () => {
  const sent = recordBids();
  server.use(
    http.get("*/ufa-bids/preview", () =>
      HttpResponse.json({ ...fine, reasons: ["Rosters are frozen for the playoffs"] }),
    ),
  );
  showPage(boss);
  fireEvent.click(row(await section("Open bids"), "Brad Marchand").getByRole("button", { name: "Raise" }));
  const form = within(await screen.findByRole("dialog"));
  fireEvent.click(form.getByLabelText("For"));
  fireEvent.click(await screen.findByRole("option", { name: "One", hidden: true }));
  fireEvent.change(await form.findByLabelText("Reason to allow it"), {
    target: { value: "Replaces the injured Snuggerud" },
  });
  fireEvent.click(form.getByRole("button", { name: "Bid" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(sent).toEqual([
    { player_id: "Brad Marchand", salary: 5, team_id: "t1", force_reason: "Replaces the injured Snuggerud" },
  ]);
});

it("says so when another bid came first, and keeps the form open", async () => {
  const first = "Another bid on Brad Marchand came first. Reload and try again.";
  recordBids(() => HttpResponse.json({ detail: { code: "conflict", error: first } }, { status: 409 }));
  server.use(http.get("*/ufa-bids/preview", () => HttpResponse.json({ ...fine, min_salary: 5 })));
  showPage();
  fireEvent.click(row(await section("Open bids"), "Brad Marchand").getByRole("button", { name: "Raise" }));
  const form = within(await screen.findByRole("dialog"));
  await form.findByText("Cap space after this bid: $209");
  fireEvent.click(form.getByRole("button", { name: "Bid" }));

  expect((await form.findByRole("alert")).textContent).toBe(first);
});

it("lets a GM withdraw their own bid while the rules allow it", async () => {
  const withdrawn: string[] = [];
  server.use(
    http.post<{ id: string }>("*/ufa-bids/:id/withdraw", ({ params }) => {
      withdrawn.push(params.id);
      return HttpResponse.json({ ...board, can_withdraw: true, bids: board.bids.slice(0, 1) });
    }),
  );
  showPage(gm, { ...board, can_withdraw: true });
  const open = await section("Open bids");
  expect(row(open, "Brad Marchand").queryByRole("button", { name: "Withdraw" })).toBeNull(); // Two's
  fireEvent.click(row(open, "David Pastrnak").getByRole("button", { name: "Withdraw" }));

  await waitForElementToBeRemoved(() => screen.queryByText("David Pastrnak"));
  expect(withdrawn).toEqual(["David Pastrnak-pending"]);
});

it("lets the admin void a bid with a reason, and lists pickups that got no winning bid", async () => {
  const voided: object[] = [];
  server.use(
    http.post<{ id: string }, { reason: string }>("*/ufa-bids/:id/void", async ({ params, request }) => {
      voided.push({ id: params.id, ...(await request.json()) });
      return HttpResponse.json(board);
    }),
  );
  showPage(boss);
  expect((await section("Pickups with no winning bid")).getByText(/Morgan Geekie/)).toBeTruthy();
  fireEvent.click(row(await section("Won: add in Fantrax"), "Charlie McAvoy").getByRole("button", { name: "Void" }));
  const form = within(await screen.findByRole("dialog"));
  fireEvent.change(form.getByLabelText("Why"), { target: { value: "Two added him first" } });
  fireEvent.click(form.getByRole("button", { name: "Void bid" }));

  await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  expect(voided).toEqual([{ id: "Charlie McAvoy-won", reason: "Two added him first" }]);
});
