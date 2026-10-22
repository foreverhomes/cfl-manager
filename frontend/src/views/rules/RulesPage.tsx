import { Anchor, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { LeagueRule } from "../../api/generated";
import { fmtMoney } from "../../format";
import { rules } from "../../queries/rules";

const AREAS = { contracts: "Contracts and the cap", draft: "Entry draft" };
const WRITTEN_RULES = "https://github.com/foreverhomes/cfl-manager/blob/main/docs/rules.md";

const season = (year: number) => `${year}/${String((year + 1) % 100).padStart(2, "0")}`;
const day = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
// GitHub's anchor for a heading: "Buyouts and Releases" is #buyouts-and-releases.
const anchor = (heading: string) =>
  heading
    .toLowerCase()
    .replace(/[^a-z0-9 -]/g, "")
    .replaceAll(" ", "-");

function show(kind: string, value: LeagueRule["value"]): string {
  if (value === null) return "Fantrax's"; // only the cap has no value of its own
  if (kind === "money") return fmtMoney(Number(value));
  if (kind === "share") return `${Number(value) * 100}%`;
  if (kind === "month_day") {
    const [month, date] = String(value).split("-").map(Number);
    return new Date(2001, month - 1, date).toLocaleDateString("en-US", { month: "long", day: "numeric" });
  }
  return String(value);
}

export function RulesPage() {
  const { data, error } = useQuery(rules);
  if (error) return <Text size="sm">Couldn't load the rules. {error.message}</Text>;
  if (!data) return null;
  return (
    <>
      <Stack gap={2} mb={12}>
        <Title order={2}>League rules</Title>
        <Text size="xs" c="dimmed">
          Their values for {season(data.year)}. A league vote changes a rule from a season on, and earlier seasons keep
          what they had.
        </Text>
      </Stack>
      {Object.entries(AREAS).map(([prefix, title]) => (
        <section key={prefix}>
          <Title order={3} mt={16} mb={4}>
            {title}
          </Title>
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="left">Rule</th>
                  <th className="left">Value</th>
                  <th className="left">Changes</th>
                </tr>
              </thead>
              <tbody>
                {data.rules
                  .filter((rule) => rule.key.startsWith(`${prefix}.`))
                  .map((rule) => (
                    <RuleRow key={rule.key} rule={rule} />
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}

function RuleRow({ rule }: { rule: LeagueRule }) {
  return (
    <tr>
      <td className="left wrap">
        {rule.label}
        <Text size="xs" c="dimmed">
          Rules: <Anchor href={`${WRITTEN_RULES}#${anchor(rule.cite)}`}>{rule.cite}</Anchor>
        </Text>
      </td>
      <td className="left">
        {show(rule.kind, rule.value)}
        {rule.value !== rule.default && (
          <Text size="xs" c="dimmed">
            Default {show(rule.kind, rule.default)}
          </Text>
        )}
      </td>
      <td className="left wrap">
        {rule.changes.length ? (
          <ul className="rule-changes">
            {rule.changes.map((c) => (
              <li key={c.changed_at + c.from_year}>
                {c.value === null ? "The default" : show(rule.kind, c.value)} from {season(c.from_year)}
                {c.note && `: ${c.note}`}
                <span className="sub">
                  {" "}
                  · {c.changed_by}, {day(c.changed_at)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <span className="sub">None</span>
        )}
      </td>
    </tr>
  );
}
