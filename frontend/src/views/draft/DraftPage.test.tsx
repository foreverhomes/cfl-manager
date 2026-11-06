// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import type { DraftBoard, DraftPick, DraftPlayer } from "../../api/generated";
import { queryClient } from "../../queries/client";
import { renderView } from "../test-utils";
import { DraftPage } from "./DraftPage";

const player = (fantrax_id: string, name: string): DraftPlayer => ({
  fantrax_id,
  name,
  position: "C",
  nhl_team: "TOR",
  nhl_draft_overall: 1,
});
const pick = (overall: number, round: number, extra: Partial<DraftPick> = {}): DraftPick => ({
  overall,
  round,
  team_id: "t2",
  team_name: "Whale Emoji",
  original_team_name: "Whale Emoji",
  state: "waiting",
  auto_source: null,
  player: null,
  ...extra,
});
const board = (extra: Partial<DraftBoard> = {}): DraftBoard => ({
  year: 2027,
  state: "open",
  now: "2027-06-20T12:00:00Z",
  on_clock: 2,
  clock_ends_at: "2027-06-20T13:00:00Z",
  clock_left_seconds: null,
  can_pick: false,
  picks: [
    pick(1, 1, { state: "made", player: player("a1", "Alex Carter") }),
    pick(2, 1, { state: "on_clock" }),
    pick(3, 2, { team_name: "Lindy Ruffryders", original_team_name: "Whale Emoji" }),
  ],
  ...extra,
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  queryClient.clear();
});
afterAll(() => server.close());

const showPage = (answer: () => Response) => {
  server.use(http.get("*/draft", answer));
  renderView(<DraftPage />);
};
const row = (name: RegExp) => screen.findByRole("row", { name });

it("lists each round's picks with the team and the player", async () => {
  showPage(() => HttpResponse.json(board()));
  expect(await screen.findByText("Round 1")).toBeTruthy();
  expect(screen.getByText("Round 2")).toBeTruthy();
  expect(within(await row(/Alex Carter/)).getByText("Whale Emoji")).toBeTruthy();
});

it("says which team a traded pick came from", async () => {
  showPage(() => HttpResponse.json(board()));
  expect(await screen.findByText("from Whale Emoji")).toBeTruthy();
});

it("marks the pick on the clock", async () => {
  showPage(() => HttpResponse.json(board()));
  expect(within(await row(/On the clock/)).getByText("2")).toBeTruthy();
});

it.each([
  ["nhl_draft", "Auto · NHL draft"],
  ["list", "Auto · list"],
] as const)("marks a pick the app made from %s as %s", async (auto_source, label) => {
  const auto = pick(1, 1, { state: "auto", auto_source, player: player("a1", "Alex Carter") });
  showPage(() => HttpResponse.json(board({ picks: [auto] })));
  expect(within(await row(/Alex Carter/)).getByText(label)).toBeTruthy();
});

it("doesn't mark a pick the team made itself", async () => {
  showPage(() => HttpResponse.json(board()));
  expect(within(await row(/Alex Carter/)).queryByText(/Auto/)).toBeNull();
});

it("counts down on the server's clock, not the browser's", async () => {
  showPage(() => HttpResponse.json(board()));
  expect((await screen.findByRole("timer")).textContent).toBe("1:00:00");
});

it("shows the time left, and no countdown, while the draft is paused", async () => {
  showPage(() => HttpResponse.json(board({ state: "paused", clock_ends_at: null, clock_left_seconds: 600 })));
  expect(await screen.findByText("0:10:00")).toBeTruthy();
  expect(screen.getByText(/Paused/)).toBeTruthy();
  expect(screen.queryByRole("timer")).toBeNull();
});

it.each([
  ["scheduled", /hasn't started/],
  ["complete", /is complete/],
] as const)("says when the draft is %s", async (state, text) => {
  showPage(() => HttpResponse.json(board({ state, on_clock: null, clock_ends_at: null })));
  expect(await screen.findByText(text)).toBeTruthy();
  expect(screen.queryByRole("timer")).toBeNull();
});

it("says there's no draft yet", async () => {
  showPage(() => HttpResponse.json({ detail: "No draft yet" }, { status: 404 }));
  expect(await screen.findByText("No draft yet.")).toBeTruthy();
});

it("says when the board can't load", async () => {
  showPage(() => HttpResponse.json({ detail: "Server error" }, { status: 500 }));
  expect(await screen.findByText(/Couldn't load the draft. Server error/)).toBeTruthy();
});

describe("the pick on the clock", () => {
  const blake = player("a2", "Blake Young");
  const nextTeam = board({ on_clock: 3, can_pick: false });

  // The server answers a pick with `answer`, and the board reads `after` from then on.
  const showForm = (answer: () => Response = () => HttpResponse.json(nextTeam), after = nextTeam) => {
    let current = board({ can_pick: true });
    const searched: string[] = [];
    const sent: unknown[] = [];
    server.use(
      http.get("*/draft", () => HttpResponse.json(current)),
      http.get("*/draft/eligible", ({ request }) => {
        searched.push(new URL(request.url).searchParams.get("q") ?? "");
        return HttpResponse.json([blake]);
      }),
      http.post("*/draft/picks", async ({ request }) => {
        sent.push(await request.json());
        current = after;
        return answer();
      }),
    );
    renderView(<DraftPage />);
    return { searched, sent };
  };
  const search = () => screen.findByRole("combobox", { name: "Find a player" });
  // Select takes a change to an unfocused input for the browser's autofill, not for typing.
  const type = async (text: string) => {
    const input = await search();
    input.focus();
    fireEvent.change(input, { target: { value: text } });
  };
  const choose = async () => fireEvent.click(await screen.findByRole("option", { name: /Blake Young/, hidden: true }));
  const draft = async () => {
    await choose();
    fireEvent.click(await screen.findByRole("button", { name: "Draft Blake Young" }));
  };

  it("has no form when the pick isn't yours", async () => {
    server.use(http.get("*/draft", () => HttpResponse.json(board())));
    renderView(<DraftPage />);
    await screen.findByText("Round 1");
    expect(screen.queryByRole("combobox", { name: "Find a player" })).toBeNull();
  });

  it("names the pick and the team", async () => {
    showForm();
    expect(await screen.findByRole("heading", { name: "Pick 2 for Whale Emoji" })).toBeTruthy();
  });

  it("lists the best players before you type", async () => {
    const { searched } = showForm();
    await search();
    await waitFor(() => expect(searched).toEqual([""]));
  });

  it("searches on the server as you type", async () => {
    const { searched } = showForm();
    await type("blak");
    await waitFor(() => expect(searched).toContain("blak"));
  });

  it("shows what the server found, without filtering it again", async () => {
    const { searched } = showForm();
    await type("yng");
    await waitFor(() => expect(searched).toContain("yng"));
    expect(await screen.findByRole("option", { name: /Blake Young/, hidden: true })).toBeTruthy();
  });

  it("asks you to confirm the player you chose, and sends nothing yet", async () => {
    const { sent } = showForm();
    await choose();
    expect(await screen.findByRole("button", { name: "Draft Blake Young" })).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it("goes back to the search", async () => {
    showForm();
    await choose();
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(await search()).toBeTruthy();
  });

  it("makes the pick when you confirm", async () => {
    const { sent } = showForm();
    await draft();
    expect(await screen.findByText("You drafted Blake Young.")).toBeTruthy();
    expect(sent).toEqual([{ overall: 2, player_id: "a2" }]);
  });

  it("shows the server's reasons and goes back to the search when it refuses the player", async () => {
    const reasons = ["He was signed in 2024"];
    showForm(
      () => HttpResponse.json({ detail: { code: "blocked", error: "No", reasons } }, { status: 400 }),
      board({ can_pick: true }),
    );
    await draft();
    expect(await screen.findByText("He was signed in 2024")).toBeTruthy();
    expect(await search()).toBeTruthy();
  });

  it.each([
    ["nhl_draft", /Time ran out\. The app picked Sam Brooks, the first NHL draftee available\./],
    ["list", /Time ran out\. The app picked Sam Brooks, the next name on the list\./],
  ] as const)("says the app picked from %s when the pick is gone", async (auto_source, text) => {
    const auto = pick(2, 1, { state: "auto", auto_source, player: player("a3", "Sam Brooks") });
    showForm(
      () => HttpResponse.json({ detail: { code: "conflict", error: "Too late" } }, { status: 409 }),
      board({ on_clock: 3, can_pick: false, picks: [auto, pick(3, 2, { state: "on_clock" })] }),
    );
    await draft();
    expect(await screen.findByText(text)).toBeTruthy();
  });

  it("stays on the player with the message when the pick fails another way", async () => {
    showForm(() => HttpResponse.json({ detail: "Server error" }, { status: 500 }), board({ can_pick: true }));
    await draft();
    expect(await screen.findByText("Server error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Draft Blake Young" })).toBeTruthy();
  });
});
