import {
  Accordion,
  Anchor,
  Badge,
  Card,
  Group,
  Input,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode, useEffect, useState } from "react";
import { Failed } from "../../api/errors";
import type { ExpiringContracts, ExpiringPlayer } from "../../api/generated";
import { openPlayer } from "../../bridge";
import { fmtMoney, fmtMonthDay, fmtNum, positions, possessive, TYPE_SHORT } from "../../format";
import { GLOSSARY } from "../../glossary";
import { expiringContracts } from "../../queries/offseason";
import { teams } from "../../queries/teams";

// What each player becomes when his contract ends.
const STATUSES = [
  {
    key: "rfa",
    label: "RFAs",
    option: "RFAs",
    sub: "restricted free agents",
    test: (p: ExpiringPlayer) => p.contract_end.status === "RFA",
  },
  {
    key: "ufa",
    label: "UFAs",
    option: "UFAs",
    sub: "unrestricted free agents",
    test: (p: ExpiringPlayer) => p.contract_end.status === "UFA",
  },
  {
    key: "unknown",
    label: "Status unknown",
    option: "Unknown",
    sub: "no birth date on file",
    test: (p: ExpiringPlayer) => !p.contract_end.status,
  },
];
const GROUPS = [
  { key: "F", label: "Forwards", sub: "C / LW / RW" },
  { key: "D", label: "Defense" },
  { key: "G", label: "Goalies" },
];
const group = (p: ExpiringPlayer) => (p.position === "G" || p.position === "D" ? p.position : "F");
// Bench is a lighter step of Active's green: still rostered, so it counts for the cap.
const ROSTER: Record<string, [label: string, color: string]> = {
  ACTIVE: ["Active", "var(--good)"],
  RESERVE: ["Bench", "color-mix(in oklab, var(--good) 45%, var(--surface))"],
  INJURED_RESERVE: ["IR", "var(--bad)"],
  MINORS: ["Prospect", "var(--prospect)"],
};

type Column = {
  key: string;
  label: string;
  value: (p: ExpiringPlayer) => string | number | null;
  show?: (p: ExpiringPlayer) => ReactNode;
  left?: boolean;
  asc?: boolean; // sorts low to high first
  help?: keyof typeof GLOSSARY;
};
type Sort = { key: string; dir: number };

const columns = (data: ExpiringContracts, oneTeam: boolean): Column[] => [
  { key: "name", label: "Player", left: true, asc: true, value: (p) => p.name },
  ...(oneTeam
    ? []
    : [
        {
          key: "fantasy_team",
          label: "Team",
          left: true,
          value: (p: ExpiringPlayer) => p.fantasy_team ?? "",
          show: (p: ExpiringPlayer) => p.fantasy_team ?? "–",
        },
      ]),
  {
    key: "age_at_expiry",
    label: `Age ${fmtMonthDay(data.ufa_day)}`,
    value: (p) => p.contract_end.age_at_expiry,
    show: (p) => p.contract_end.age_at_expiry ?? "–",
  },
  { key: "contract_type", label: "Type", left: true, value: (p) => TYPE_SHORT[p.contract_type] ?? "" },
  {
    key: "final_salary",
    label: `${data.after_season} salary`,
    value: (p) => p.final_salary,
    show: (p) => fmtMoney(p.final_salary),
  },
  // Every contract ending in the next offseason has one season left, so the column only helps later ones.
  ...(data.year === data.offseasons[0].year
    ? []
    : [{ key: "contract_years", label: "Yrs left", value: (p: ExpiringPlayer) => p.contract_years }]),
  { key: "value", label: "Value", help: "value", value: (p) => p.value, show: (p) => fmtNum(p.value, 2) },
  {
    key: "trade_rank",
    label: "Trade rank",
    help: "trade_rank",
    asc: true,
    value: (p) => p.trade_rank,
    show: (p) => (p.trade_rank ? `#${p.trade_rank}` : "–"),
  },
  {
    key: "gp_this_season",
    label: "GP (this/last)",
    help: "gp",
    value: (p) => p.gp_this_season,
    show: (p) => `${p.gp_this_season}/${p.gp_last_season}`,
  },
];

// A player without a value goes last, whichever way the column sorts.
const compare = (column: Column, dir: number) => (a: ExpiringPlayer, b: ExpiringPlayer) => {
  const [va, vb] = [column.value(a), column.value(b)];
  if (va === null || vb === null) return va === vb ? 0 : va === null ? 1 : -1;
  return (typeof va === "string" ? va.localeCompare(String(vb)) : va - Number(vb)) * dir;
};
const salaries = (list: ExpiringPlayer[]) => fmtMoney(list.reduce((sum, p) => sum + p.final_salary, 0));
const count = (n: number) => `${n} player${n === 1 ? "" : "s"}`;

export function OffseasonPage() {
  const [year, setYear] = useState<number | null>(null); // null: the next offseason
  const [team, setTeam] = useState("all");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState<Sort>({ key: "final_salary", dir: -1 });
  const { data, error } = useQuery(expiringContracts(year));
  const league = useQuery(teams).data ?? [];
  // A year picked earlier can fall out of range once the league moves on a season: go back to the next one.
  useEffect(() => {
    if (error instanceof Failed && error.status === 400 && year !== null) setYear(null);
  }, [error, year]);
  if (error) return <Text size="sm">Couldn't load the expiring contracts. {error.message}</Text>;
  if (!data) return null;

  const onTeam = (p: { fantasy_team_id: string }) => team === "all" || p.fantasy_team_id === team;
  const players = data.players.filter(onTeam);
  const missing = data.no_contract.filter(onTeam);
  const statuses = STATUSES.filter((s) => s.key !== "unknown" || data.players.some(s.test));
  const shown = statuses.some((s) => s.key === status) ? status : "all";
  const teamName = league.find((t) => t.id === team)?.name ?? team;
  return (
    <Stack gap={16}>
      <Group align="flex-end" gap="12px 16px">
        <Select
          label="Offseason"
          data={data.offseasons.map((o) => ({ value: String(o.year), label: `${o.year} (after ${o.after_season})` }))}
          value={String(year ?? data.year)}
          onChange={(value) => setYear(Number(value))}
          allowDeselect={false}
        />
        <Select
          label="Team"
          data={[{ value: "all", label: "All teams" }, ...league.map((t) => ({ value: t.id, label: t.name }))]}
          value={team}
          onChange={(value) => setTeam(value ?? "all")}
          allowDeselect={false}
        />
        <Input.Wrapper label="Show">
          <SegmentedControl
            data={[{ value: "all", label: "All" }, ...statuses.map((s) => ({ value: s.key, label: s.option }))]}
            value={shown}
            onChange={setStatus}
          />
        </Input.Wrapper>
      </Group>
      <SimpleGrid minColWidth={150} autoFlow="auto-fit" spacing={12}>
        {statuses.map((s) => {
          const list = players.filter(s.test);
          if (s.key === "unknown" && !list.length) return null;
          return (
            <Card key={s.key} py={14}>
              <Text size="xs" c="var(--ink-2)">
                {s.label}
              </Text>
              <Text fz={26} fw={600} lh={1.15} mt={2}>
                {list.length}
                <Text span fz="md" fw={500} c="dimmed" ml={4}>
                  {list.length === 1 ? "player" : "players"}
                </Text>
              </Text>
              <Text size="xs" c="dimmed" mt={4}>
                {s.key === "unknown"
                  ? "Add birth dates to place them"
                  : `${salaries(list)} in ${data.after_season} salaries`}
              </Text>
            </Card>
          );
        })}
      </SimpleGrid>
      <Card>
        <Stack gap={2} mb={12}>
          <Title order={2}>
            {team === "all" ? "All" : possessive(teamName)} contracts ending after {data.after_season}
          </Title>
          <Text size="xs" c="dimmed">
            Free agency is decided on {fmtMonthDay(data.ufa_day)}, {data.year}: players {data.ufa_age} or older become
            UFAs, younger players RFAs, and in-season pickups are always UFAs. Click a player to open his contract.
          </Text>
        </Stack>
        <ExpiringTable
          players={players}
          columns={columns(data, team !== "all")}
          statuses={statuses.filter((s) => shown === "all" || s.key === shown)}
          sort={sort}
          onSort={setSort}
          empty={`No contracts on file end after ${data.after_season}.`}
        />
        {missing.length > 0 && (
          <Accordion chevronPosition="left" mt={12}>
            <Accordion.Item value="missing">
              <Accordion.Control>
                {missing.length} rostered player{missing.length === 1 ? " has" : "s have"} no contract on file, so they
                can't be placed in any offseason
              </Accordion.Control>
              <Accordion.Panel>
                <Group gap="4px 14px">
                  {missing.map((p) => (
                    <PlayerLink key={p.fantrax_id} player={p} />
                  ))}
                </Group>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
        )}
      </Card>
    </Stack>
  );
}

function ExpiringTable({
  players,
  columns,
  statuses,
  sort,
  onSort,
  empty,
}: {
  players: ExpiringPlayer[];
  columns: Column[];
  statuses: typeof STATUSES;
  sort: Sort;
  onSort: (sort: Sort) => void;
  empty: string;
}) {
  const sections = statuses.map((s) => ({ ...s, players: players.filter(s.test) })).filter((s) => s.players.length);
  if (!sections.length) {
    return (
      <Text size="sm" c="dimmed" ta="center" p={16}>
        {empty}
      </Text>
    );
  }
  const byColumn = compare(columns.find((c) => c.key === sort.key) ?? columns[0], sort.dir);
  const heading = (label: ReactNode, meta: string) => (
    <Group justify="space-between" wrap="nowrap">
      <span>{label}</span>
      <Text span inherit fw={400} c="dimmed" tt="none" style={{ letterSpacing: 0 }}>
        {meta}
      </Text>
    </Group>
  );
  return (
    <Table.ScrollContainer minWidth="max-content" type="native">
      <Table style={{ whiteSpace: "nowrap" }}>
        <Table.Thead>
          <Table.Tr>
            {columns.map((c) => (
              <Table.Th
                key={c.key}
                ta={c.left ? "left" : "right"}
                aria-sort={c.key === sort.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
              >
                <SortButton column={c} sort={sort} onSort={onSort} />
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        {sections.map((s, i) => (
          <Fragment key={s.key}>
            <Table.Tbody>
              <Table.Tr>
                <Table.Th
                  colSpan={columns.length}
                  fz="md"
                  fw={600}
                  c="var(--ink)"
                  pt={i ? 18 : 6}
                  style={{ borderBottom: "2px solid var(--ink)" }}
                >
                  {heading(
                    <>
                      {s.label}
                      <Text span inherit fw={400} c="dimmed">
                        {" "}
                        · {s.sub}
                      </Text>
                    </>,
                    `${count(s.players.length)} · ${salaries(s.players)} salary`,
                  )}
                </Table.Th>
              </Table.Tr>
            </Table.Tbody>
            {GROUPS.map((g) => {
              const list = s.players.filter((p) => group(p) === g.key).sort(byColumn);
              if (!list.length) return null;
              return (
                <Table.Tbody key={g.key}>
                  <Table.Tr>
                    <Table.Th
                      colSpan={columns.length}
                      fw={600}
                      c="var(--ink-2)"
                      tt="uppercase"
                      pt={6}
                      pb={4}
                      style={{ letterSpacing: "0.04em" }}
                    >
                      {heading(
                        <>
                          {g.label}
                          {g.sub && (
                            <Text span inherit fw={400} c="dimmed" tt="none" style={{ letterSpacing: 0 }}>
                              {" "}
                              {g.sub}
                            </Text>
                          )}
                        </>,
                        `${list.length} · ${salaries(list)}`,
                      )}
                    </Table.Th>
                  </Table.Tr>
                  {list.map((p) => (
                    <PlayerRow key={p.fantrax_id} player={p} columns={columns} />
                  ))}
                </Table.Tbody>
              );
            })}
          </Fragment>
        ))}
      </Table>
    </Table.ScrollContainer>
  );
}

function SortButton({ column, sort, onSort }: { column: Column; sort: Sort; onSort: (sort: Sort) => void }) {
  const sorted = column.key === sort.key;
  const button = (
    <UnstyledButton
      fz="inherit"
      fw="inherit"
      c="inherit"
      td={column.help && "underline dotted"}
      style={{ textUnderlineOffset: 3 }}
      onClick={() =>
        onSort(sorted ? { key: column.key, dir: -sort.dir } : { key: column.key, dir: column.asc ? 1 : -1 })
      }
    >
      {column.label}
      {sorted && (sort.dir === 1 ? " ↑" : " ↓")}
    </UnstyledButton>
  );
  if (!column.help) return button;
  const [term, text] = GLOSSARY[column.help];
  return (
    <Tooltip
      label={
        <>
          <Text fz="md" fw={600}>
            {term}
          </Text>
          <Text inherit c="var(--ink-2)">
            {text}
          </Text>
        </>
      }
      events={{ hover: true, focus: true, touch: false }}
    >
      {button}
    </Tooltip>
  );
}

function PlayerRow({ player, columns }: { player: ExpiringPlayer; columns: Column[] }) {
  const [label, color] = ROSTER[player.roster_status ?? ""] ?? [player.roster_status ?? "–", "var(--muted)"];
  return (
    <Table.Tr>
      {columns.map((c) =>
        c.key === "name" ? (
          <Table.Td key={c.key}>
            <PlayerLink player={player} />
            <Badge color={color} ml={8}>
              {label}
            </Badge>
            <Text size="xs" c="dimmed">
              {[positions(player), player.nhl_team].filter(Boolean).join(" · ")}
            </Text>
          </Table.Td>
        ) : (
          <Table.Td key={c.key} ta={c.left ? "left" : "right"}>
            {c.show ? c.show(player) : c.value(player)}
          </Table.Td>
        ),
      )}
    </Table.Tr>
  );
}

function PlayerLink({ player }: { player: { fantrax_id: string; name: string } }) {
  return (
    <Anchor
      component="button"
      type="button"
      title="Edit contract"
      fw={500}
      c="var(--ink)"
      underline="always"
      style={{ textDecorationColor: "var(--axis)", textUnderlineOffset: 3 }}
      onClick={() => openPlayer(player.fantrax_id)}
    >
      {player.name}
    </Anchor>
  );
}
