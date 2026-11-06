// A draft that runs in the browser on a short clock, to try the room until the backend lands. Delete it then.
// Run npm run dev, then open /?mock-draft#draft. dev-public/mockServiceWorker.js comes from `npx msw init dev-public`.
import { HttpResponse, http } from "msw";
import { setupWorker } from "msw/browser";
import type { DraftBoard, DraftPick, DraftPlayer, NewDraftPick } from "../../api/generated";
import { refreshDraft } from "../../queries/draft";

const YOU = "Your Team";
const YOUR_CLOCK = 45_000;
const THEIR_CLOCK = 5_000;

const NAMES =
  "Alex Carter,Blake Young,Sam Brooks,Jordan Reid,Casey Lund,Riley Moss,Drew Hart,Morgan Vale,Quinn Ash,Taylor Boyd";
const players: DraftPlayer[] = NAMES.split(",").map((name, i) => ({
  fantrax_id: `mock${i + 1}`,
  name,
  position: ["C", "LW", "D", "RW", "G"][i % 5],
  nhl_team: ["TOR", "MTL", "VAN", "EDM"][i % 4],
  nhl_draft_overall: i + 1,
}));

const pick = (overall: number, round: number, team_name: string, original_team_name = team_name): DraftPick => ({
  overall,
  round,
  team_id: team_name,
  team_name,
  original_team_name,
  state: overall === 1 ? "on_clock" : "waiting",
  auto_source: null,
  player: null,
});

let picks = [
  pick(1, 1, "Whale Emoji"),
  pick(2, 1, YOU),
  pick(3, 1, YOU, "Lindy Ruffryders"),
  pick(4, 1, "Goal Diggers"),
  pick(5, 2, "Whale Emoji"),
  pick(6, 2, YOU),
  pick(7, 2, "Lindy Ruffryders"),
  pick(8, 2, "Goal Diggers"),
];
let overall = 1;
let endsAt = Date.now() + THEIR_CLOCK;

const onClock = () => picks.find((p) => p.overall === overall);
const available = () => players.filter((p) => !picks.some((taken) => taken.player?.fantrax_id === p.fantrax_id));

function finish(player: DraftPlayer, state: "made" | "auto", auto_source: DraftPick["auto_source"], at: number) {
  picks = picks.map((p) => {
    if (p.overall === overall) return { ...p, player, state, auto_source };
    return p.overall === overall + 1 ? { ...p, state: "on_clock" } : p;
  });
  overall += 1;
  endsAt = at + (onClock()?.team_name === YOU ? YOUR_CLOCK : THEIR_CLOCK);
}

// What the server will do on each read. The next clock starts at the deadline, not when this ran.
function applyDeadlines() {
  for (let team = onClock()?.team_name; team && Date.now() >= endsAt; team = onClock()?.team_name) {
    if (team === YOU) finish(available()[0], "auto", "nhl_draft", endsAt);
    else if (team === "Goal Diggers") finish(available()[0], "auto", "list", endsAt);
    else finish(available()[0], "made", null, endsAt);
  }
}

const board = (): DraftBoard => ({
  year: 2027,
  state: onClock() ? "open" : "complete",
  now: new Date().toISOString(),
  on_clock: onClock()?.overall ?? null,
  clock_ends_at: onClock() ? new Date(endsAt).toISOString() : null,
  clock_left_seconds: null,
  can_pick: onClock()?.team_name === YOU,
  picks,
});

const worker = setupWorker(
  http.get("*/draft", () => {
    applyDeadlines();
    return HttpResponse.json(board());
  }),
  http.get("*/draft/eligible", ({ request }) => {
    applyDeadlines();
    const q = new URL(request.url).searchParams.get("q")?.toLowerCase() ?? "";
    return HttpResponse.json(available().filter((p) => p.name.toLowerCase().includes(q)));
  }),
  http.post<never, NewDraftPick>("*/draft/picks", async ({ request }) => {
    applyDeadlines();
    const sent = await request.json();
    const player = available().find((p) => p.fantrax_id === sent.player_id);
    if (sent.overall !== onClock()?.overall) {
      return HttpResponse.json({ detail: { code: "conflict", error: "That pick is gone" } }, { status: 409 });
    }
    if (!player) {
      const reasons = ["He has been drafted"];
      return HttpResponse.json({ detail: { code: "blocked", error: "Can't draft him", reasons } }, { status: 400 });
    }
    finish(player, "made", null, Date.now());
    return HttpResponse.json(board());
  }),
);

export async function startDraftMock() {
  await worker.start({ onUnhandledFrame: "bypass", quiet: true });
  setInterval(refreshDraft, 3000); // the real board polls every 30 seconds, too slow to watch this short clock
}
