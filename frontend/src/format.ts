type Num = number | null | undefined;

export function ordinal(n: Num): string {
  if (n == null) return "–";
  const s = ["th", "st", "nd", "rd"],
    v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

export function fmtCat(cat: string, v: Num): string {
  if (v == null) return "–";
  if (cat === "SV%") return v.toFixed(3).replace(/^0\./, ".");
  if (cat === "GAA") return v.toFixed(2);
  return Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(1);
}

export function fmtNum(v: Num, digits = 1): string {
  return v == null ? "–" : Number(v).toFixed(digits);
}

export function fmtMoney(v: Num): string {
  if (v == null) return "–";
  const n = Math.round(Number(v) * 10) / 10;
  return (n < 0 ? "−$" : "$") + Math.abs(n).toString();
}

export function fmtSigned(v: Num, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "–";
  const r = Number(v.toFixed(digits));
  if (r === 0) return (0).toFixed(digits);
  return (r > 0 ? "+" : "−") + Math.abs(r).toFixed(digits);
}

// A date-only string is midnight UTC, so the day is formatted in UTC too.
const monthDayFormat = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" });
export const fmtMonthDay = (monthDay: string) => monthDayFormat.format(new Date(`2001-${monthDay}`));

// h:mm:ss, rounded up, so it reads 0:00:00 only when the time is up.
export function fmtClock(ms: number): string {
  const seconds = Math.max(Math.ceil(ms / 1000), 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${Math.floor(seconds / 3600)}:${pad(Math.floor(seconds / 60) % 60)}:${pad(seconds % 60)}`;
}

export function possessive(name: string): string {
  return /s$/i.test(name) ? `${name}’` : `${name}’s`;
}

export function positions(p: { eligible?: ReadonlyArray<string> | null; position?: string | null }): string {
  return (p.eligible ?? []).filter((e) => e !== "F").join(",") || p.position || "";
}

export const TYPE_SHORT: Record<string, string | null> = { STANDARD: null, ELC: "ELC", IN_SEASON_UFA: "In-season UFA" };
