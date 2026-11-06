import { Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useInterval } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useMachine } from "@xstate/react";
import { useEffect, useState } from "react";
import { Failed } from "../../api/errors";
import type { DraftBoard, DraftPick } from "../../api/generated";
import { fmtClock } from "../../format";
import { draftPick, localDeadline } from "../../machines/draftPick";
import { draftBoard } from "../../queries/draft";
import { PickPanel, playerFacts } from "./PickPanel";

const AUTO_SOURCE = { list: "list", nhl_draft: "NHL draft" };

export function DraftPage() {
  const { data: board, dataUpdatedAt, error } = useQuery(draftBoard);
  const [state, send] = useMachine(draftPick);
  useEffect(() => {
    if (board) send({ type: "BOARD", board, receivedAt: dataUpdatedAt });
  }, [board, dataUpdatedAt, send]);
  if (!board) {
    if (error instanceof Failed && error.status === 404) return <Text size="sm">No draft yet.</Text>;
    return error ? <Text size="sm">Couldn't load the draft. {error.message}</Text> : null;
  }
  return (
    <Stack gap={16}>
      <DraftStatus board={board} receivedAt={dataUpdatedAt} />
      <PickPanel board={board} state={state} send={send} />
      <Card>
        <DraftTable picks={board.picks} />
      </Card>
    </Stack>
  );
}

function DraftStatus({ board, receivedAt }: { board: DraftBoard; receivedAt: number }) {
  const pick = board.picks.find((p) => p.overall === board.on_clock);
  const onClock = pick ? `pick ${pick.overall}, ${pick.team_name}` : "";
  const status = {
    scheduled: "The draft hasn't started.",
    open: `On the clock: ${onClock}`,
    paused: `Paused at ${onClock}`,
    complete: "The draft is complete.",
  };
  return (
    <Card>
      <Group justify="space-between" wrap="nowrap">
        <Stack gap={2}>
          <Title order={2}>{board.year} entry draft</Title>
          <Text size="sm" c="var(--ink-2)">
            {status[board.state]}
          </Text>
        </Stack>
        {board.state === "open" && board.clock_ends_at && <Countdown endsAt={localDeadline(board, receivedAt)} />}
        {board.state === "paused" && board.clock_left_seconds !== null && (
          <ClockText>{fmtClock(board.clock_left_seconds * 1000)}</ClockText>
        )}
      </Group>
    </Card>
  );
}

function Countdown({ endsAt }: { endsAt: number }) {
  const [now, setNow] = useState(Date.now);
  useInterval(() => setNow(Date.now()), 1000, { autoInvoke: true });
  return <ClockText role="timer">{fmtClock(endsAt - now)}</ClockText>;
}

function ClockText({ children, role }: { children: string; role?: string }) {
  return (
    <Text role={role} fz={26} fw={600} lh={1.15} style={{ fontVariantNumeric: "tabular-nums" }}>
      {children}
    </Text>
  );
}

function DraftTable({ picks }: { picks: ReadonlyArray<DraftPick> }) {
  const rounds = [...new Set(picks.map((p) => p.round))];
  return (
    <Table.ScrollContainer minWidth="max-content" type="native">
      <Table style={{ whiteSpace: "nowrap" }}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th ta="right">Pick</Table.Th>
            <Table.Th>Team</Table.Th>
            <Table.Th>Player</Table.Th>
          </Table.Tr>
        </Table.Thead>
        {rounds.map((round) => (
          <Table.Tbody key={round}>
            <Table.Tr>
              <Table.Th colSpan={3} fz="md" fw={600} c="var(--ink)" pt={round === rounds[0] ? 6 : 18}>
                Round {round}
              </Table.Th>
            </Table.Tr>
            {picks
              .filter((p) => p.round === round)
              .map((p) => (
                <PickRow key={p.overall} pick={p} />
              ))}
          </Table.Tbody>
        ))}
      </Table>
    </Table.ScrollContainer>
  );
}

function PickRow({ pick }: { pick: DraftPick }) {
  return (
    <Table.Tr bg={pick.state === "on_clock" ? "var(--wash)" : undefined}>
      <Table.Td ta="right">{pick.overall}</Table.Td>
      <Table.Td>
        {pick.team_name}
        {pick.original_team_name !== pick.team_name && (
          <Text size="xs" c="dimmed">
            from {pick.original_team_name}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {pick.player && (
          <>
            {pick.player.name}
            <Text span size="xs" c="dimmed" ml={8}>
              {playerFacts(pick.player)}
            </Text>
          </>
        )}
        {pick.state === "on_clock" && <Badge color="var(--accent)">On the clock</Badge>}
        {pick.auto_source && (
          <Badge color="var(--warn)" ml={8}>
            Auto · {AUTO_SOURCE[pick.auto_source]}
          </Badge>
        )}
      </Table.Td>
    </Table.Tr>
  );
}
