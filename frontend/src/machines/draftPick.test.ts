import { describe, expect, it, vi } from "vitest";
import { createActor, fromPromise, SimulatedClock, waitFor } from "xstate";
import { Blocked, Conflict, Failed } from "../api/errors";
import type { DraftBoard, DraftPick, DraftPlayer, NewDraftPick } from "../api/generated";
import { draftPick } from "./draftPick";

const HOUR = 3_600_000;
const player: DraftPlayer = {
  fantrax_id: "p1",
  name: "Alex Carter",
  position: "C",
  nhl_team: "TOR",
  nhl_draft_overall: 1,
};
const pick = (overall: number, state: DraftPick["state"]): DraftPick => ({
  overall,
  round: 1,
  team_id: "t1",
  team_name: "Whale Emoji",
  original_team_name: "Whale Emoji",
  state,
  auto_source: state === "auto" ? "nhl_draft" : null,
  player: state === "auto" || state === "made" ? player : null,
});
// Far from the browser's clock, so a test can't pass by reading Date.now() as the server's time.
const board = (on_clock: number | null, extra: Partial<DraftBoard> = {}): DraftBoard => ({
  year: 2027,
  state: "open",
  now: "2027-06-20T12:00:00Z",
  on_clock,
  clock_ends_at: on_clock ? "2027-06-20T13:00:00Z" : null,
  clock_left_seconds: null,
  can_pick: true,
  picks: [pick(1, on_clock === 1 ? "on_clock" : "made"), pick(2, on_clock === 2 ? "on_clock" : "waiting")],
  ...extra,
});
const BOARD = (b: DraftBoard) => ({ type: "BOARD", board: b, receivedAt: Date.now() }) as const;
const SELECT = { type: "SELECT", player } as const;
const BACK = { type: "BACK" } as const;
const CONFIRM = { type: "CONFIRM" } as const;

const myTurn = BOARD(board(1));
const nextIsMine = BOARD(board(2));
const nextIsTheirs = BOARD(board(2, { can_pick: false }));
const paused = BOARD(board(1, { state: "paused", clock_ends_at: null, clock_left_seconds: 600 }));
const autoPicked = BOARD(board(2, { can_pick: false, picks: [pick(1, "auto"), pick(2, "on_clock")] }));

type MakePick = (args: { input: NewDraftPick }) => Promise<DraftBoard>;

function start(makePick: MakePick = async () => board(2, { can_pick: false })) {
  const clock = new SimulatedClock();
  const refresh = vi.fn();
  const changed = vi.fn();
  const actor = createActor(
    draftPick.provide({ actions: { refresh, dataChanged: changed }, actors: { makePick: fromPromise(makePick) } }),
    { clock },
  ).start();
  return { actor, clock, refresh, changed, state: () => actor.getSnapshot() };
}

const hang: MakePick = () => new Promise(() => {});
const fail =
  (error: Error): MakePick =>
  () =>
    Promise.reject(error);

describe("moves", () => {
  it.each([
    ["waiting", [], myTurn, { turn: "choosing" }],
    ["waiting", [], nextIsTheirs, "waiting"],
    ["waiting", [], paused, "waiting"],
    ["choosing", [myTurn], SELECT, { turn: "confirming" }],
    ["choosing", [myTurn], myTurn, { turn: "choosing" }],
    ["choosing", [myTurn], autoPicked, "missed"],
    ["choosing", [myTurn], nextIsTheirs, "waiting"],
    ["choosing", [myTurn], paused, "waiting"],
    ["confirming", [myTurn, SELECT], BACK, { turn: "choosing" }],
    ["confirming", [myTurn, SELECT], myTurn, { turn: "confirming" }],
    ["confirming", [myTurn, SELECT], autoPicked, "missed"],
    ["confirming", [myTurn, SELECT], nextIsTheirs, "waiting"],
  ])("from %s, after %j, %j goes to %j", (_from, path, event, to) => {
    const { actor, state } = start();
    for (const step of path) actor.send(step);
    actor.send(event);
    expect(state().value).toEqual(to);
  });

  it("starts a new turn when a board puts another of my picks on the clock", () => {
    const { actor, state } = start();
    actor.send(myTurn);
    actor.send(nextIsMine);
    expect(state().value).toEqual({ turn: "choosing" });
    expect(state().context.overall).toBe(2);
  });

  it("forgets the player chosen when you go back to the search", () => {
    const { actor, state } = start();
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(BACK);
    expect(state().context.player).toBeNull();
  });

  it("forgets the player chosen when a turn ends and another starts", () => {
    const { actor, state } = start();
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(nextIsMine);
    expect(state().context.player).toBeNull();
  });
});

describe("making the pick", () => {
  it("sends the pick on the clock and the player chosen, then reloads every view", async () => {
    const makePick = vi.fn<MakePick>(async () => board(2, { can_pick: false }));
    const { actor, changed } = start(makePick);
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches("picked"));
    expect(makePick.mock.calls[0][0].input).toEqual({ overall: 1, player_id: "p1" });
    expect(changed).toHaveBeenCalledOnce();
  });

  it("goes back to the search with the reasons when the server refuses the player", async () => {
    const { actor, refresh, state } = start(fail(new Blocked({ reasons: ["He was signed in 2024"] })));
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches({ turn: "choosing" }));
    expect(state().context.reasons).toEqual(["He was signed in 2024"]);
    expect(state().context.player).toBeNull();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("asks the server what happened when the pick is gone", async () => {
    const { actor, refresh } = start(fail(new Conflict({ message: "It's auto" })));
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches("checking"));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("stays on the player with the message for any other failure", async () => {
    const { actor, state } = start(fail(new Failed({ status: 500, message: "Server error" })));
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches({ turn: "confirming" }));
    expect(state().context.error).toBe("Server error");
    expect(state().context.player).toEqual(player);
  });

  it("clears the last failure when it tries again", async () => {
    const { actor, state } = start(fail(new Failed({ status: 500, message: "Server error" })));
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches({ turn: "confirming" }));
    actor.send(CONFIRM);
    expect(state().context.error).toBeNull();
  });

  it("waits for the server's answer whatever a poll says meanwhile", () => {
    const { actor, state } = start(hang);
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    actor.send(autoPicked);
    expect(state().value).toEqual({ turn: "submitting" });
  });
});

describe("the clock", () => {
  it.each([
    ["choosing", [myTurn]],
    ["confirming", [myTurn, SELECT]],
  ])("asks the server at the deadline, measured on the server's clock, while %s", (_name, path) => {
    const { actor, clock, refresh, state } = start();
    for (const step of path) actor.send(step);
    clock.increment(HOUR - 2000);
    expect(state().matches("turn")).toBe(true);
    clock.increment(2000);
    expect(state().value).toBe("checking");
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("doesn't interrupt a pick that is on its way to the server", () => {
    const { actor, clock, state } = start(hang);
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    clock.increment(2 * HOUR);
    expect(state().value).toEqual({ turn: "submitting" });
  });

  it("waits a second before it asks again about a deadline that has passed", () => {
    const { actor, clock, state } = start();
    actor.send(BOARD(board(1, { clock_ends_at: "2027-06-20T12:00:00Z" })));
    clock.increment(999);
    expect(state().value).toEqual({ turn: "choosing" });
    clock.increment(1);
    expect(state().value).toBe("checking");
  });

  it.each([
    ["the app picked", autoPicked, "missed"],
    ["the pick is still on the clock", myTurn, { turn: "choosing" }],
    ["someone else made the pick", nextIsTheirs, "waiting"],
  ])("after checking, when %s", (_name, event, to) => {
    const { actor, clock, state } = start();
    actor.send(myTurn);
    clock.increment(HOUR);
    actor.send(event);
    expect(state().value).toEqual(to);
  });
});

describe("after the pick", () => {
  it.each([
    ["stays on the message when the next pick is theirs", nextIsTheirs, "picked"],
    ["starts a new turn when the next pick is mine", nextIsMine, { turn: "choosing" }],
  ])("picked: %s", async (_name, event, to) => {
    const { actor, state } = start();
    actor.send(myTurn);
    actor.send(SELECT);
    actor.send(CONFIRM);
    await waitFor(actor, (s) => s.matches("picked"));
    actor.send(event);
    expect(state().value).toEqual(to);
  });

  it.each([
    ["stays on the message when the next pick is theirs", nextIsTheirs, "missed"],
    ["starts a new turn when the next pick is mine", nextIsMine, { turn: "choosing" }],
  ])("missed: %s", (_name, event, to) => {
    const { actor, state } = start();
    actor.send(myTurn);
    actor.send(autoPicked);
    actor.send(event);
    expect(state().value).toEqual(to);
  });
});
