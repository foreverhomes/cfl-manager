import { assertEvent, assign, fromPromise, setup } from "xstate";
import { cfl, run } from "../api/client";
import { Blocked, Conflict } from "../api/errors";
import type { DraftBoard, DraftPlayer, NewDraftPick } from "../api/generated";
import { dataChanged } from "../bridge";
import { refreshDraft } from "../queries/draft";

type Context = {
  overall: number; // the pick this turn is for. 0 before the first turn: picks count from 1
  endsAt: number;
  player: DraftPlayer | null;
  reasons: ReadonlyArray<string>;
  error: string | null;
};

const isMyPick = (board: DraftBoard) => board.state === "open" && board.on_clock !== null && board.can_pick;

// The deadline on this browser's clock, so a wrong clock on a GM's computer can't change the time left.
export const localDeadline = (board: DraftBoard, receivedAt: number) =>
  receivedAt + Date.parse(board.clock_ends_at ?? board.now) - Date.parse(board.now);

// The clock only asks the server. GET /draft applies a deadline that has passed, and the board says what happened.
export const draftPick = setup({
  types: {
    context: {} as Context,
    events: {} as
      | { type: "BOARD"; board: DraftBoard; receivedAt: number }
      | { type: "SELECT"; player: DraftPlayer }
      | { type: "BACK" }
      | { type: "CONFIRM" },
  },
  // At least a second, so a deadline that has passed can't make the machine ask in a tight loop.
  delays: { clock: ({ context }) => Math.max(context.endsAt - Date.now(), 1000) },
  guards: {
    myPick: ({ event }) => {
      assertEvent(event, "BOARD");
      return isMyPick(event.board);
    },
    sameTurn: ({ context, event }) => {
      assertEvent(event, "BOARD");
      return isMyPick(event.board) && event.board.on_clock === context.overall;
    },
    autoPicked: ({ context, event }) => {
      assertEvent(event, "BOARD");
      return event.board.picks.some((p) => p.overall === context.overall && p.state === "auto");
    },
  },
  actions: {
    startTurn: assign(({ event }) => {
      assertEvent(event, "BOARD");
      return {
        overall: event.board.on_clock ?? 0,
        endsAt: localDeadline(event.board, event.receivedAt),
        player: null,
        reasons: [],
        error: null,
      };
    }),
    refresh: refreshDraft,
    dataChanged,
  },
  actors: {
    makePick: fromPromise(({ input, signal }: { input: NewDraftPick; signal: AbortSignal }) =>
      run(cfl.makeDraftPick({ payload: input }), signal),
    ),
  },
}).createMachine({
  id: "draftPick",
  context: { overall: 0, endsAt: 0, player: null, reasons: [], error: null },
  initial: "waiting",
  states: {
    waiting: { on: { BOARD: { guard: "myPick", target: "turn" } } },
    turn: {
      entry: "startTurn",
      initial: "choosing",
      on: {
        BOARD: [
          { guard: "sameTurn" },
          { guard: "autoPicked", target: "missed" },
          { guard: "myPick", target: "turn", reenter: true },
          { target: "waiting" },
        ],
      },
      states: {
        choosing: {
          after: { clock: "#draftPick.checking" },
          on: {
            SELECT: {
              target: "confirming",
              actions: assign({ player: ({ event }) => event.player, reasons: [] }),
            },
          },
        },
        confirming: {
          after: { clock: "#draftPick.checking" },
          on: { BACK: { target: "choosing", actions: assign({ player: null }) }, CONFIRM: "submitting" },
        },
        submitting: {
          entry: assign({ error: null }),
          on: { BOARD: {} }, // the POST's answer decides, not a poll
          invoke: {
            src: "makePick",
            input: ({ context }) => ({ overall: context.overall, player_id: context.player?.fantrax_id ?? "" }),
            onDone: "#draftPick.picked",
            onError: [
              {
                guard: ({ event }) => event.error instanceof Blocked,
                target: "choosing",
                actions: [
                  assign({
                    player: null,
                    reasons: ({ event }) => (event.error instanceof Blocked ? event.error.reasons : []),
                  }),
                  "refresh",
                ],
              },
              { guard: ({ event }) => event.error instanceof Conflict, target: "#draftPick.checking" },
              {
                target: "confirming",
                actions: assign({
                  error: ({ event }) =>
                    (event.error instanceof Error && event.error.message) || "Couldn't make the pick",
                }),
              },
            ],
          },
        },
      },
    },
    checking: {
      entry: "refresh",
      on: {
        BOARD: [{ guard: "autoPicked", target: "missed" }, { guard: "myPick", target: "turn" }, { target: "waiting" }],
      },
    },
    picked: { entry: "dataChanged", on: { BOARD: { guard: "myPick", target: "turn" } } },
    missed: { on: { BOARD: { guard: "myPick", target: "turn" } } },
  },
});
