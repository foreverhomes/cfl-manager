import { Alert, Button, Card, Group, Loader, Select, Stack, Text, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { EventFrom, SnapshotFrom } from "xstate";
import type { DraftBoard, DraftPlayer } from "../../api/generated";
import type { draftPick } from "../../machines/draftPick";
import { eligiblePlayers } from "../../queries/draft";

const AUTO_REASON = { list: "the next name on the list", nhl_draft: "the first NHL draftee available" };

export const playerFacts = (p: DraftPlayer) =>
  [p.position, p.nhl_team, p.nhl_draft_overall && `NHL draft #${p.nhl_draft_overall}`].filter(Boolean).join(" · ");

export function PickPanel({
  board,
  state,
  send,
}: {
  board: DraftBoard;
  state: SnapshotFrom<typeof draftPick>;
  send: (event: EventFrom<typeof draftPick>) => void;
}) {
  const { overall, player, reasons, error } = state.context;
  const pick = board.picks.find((p) => p.overall === overall);
  if (state.matches("checking")) {
    return (
      <Alert color="var(--warn)">
        <Group gap={8}>
          <Loader size="xs" /> Time's up. Checking the board.
        </Group>
      </Alert>
    );
  }
  if (state.matches("picked")) return <Alert color="var(--good)">You drafted {player?.name}.</Alert>;
  if (state.matches("missed")) {
    return (
      <Alert color="var(--warn)">
        Time ran out. The app picked {pick?.player?.name}
        {pick?.auto_source && `, ${AUTO_REASON[pick.auto_source]}`}.
      </Alert>
    );
  }
  if (!state.matches("turn")) return null;
  return (
    <Card>
      <Stack gap={12}>
        <Title order={2}>
          Pick {overall} for {pick?.team_name}
        </Title>
        {player ? (
          <Stack gap={12} align="flex-start">
            <div>
              <Text fw={500}>{player.name}</Text>
              <Text size="xs" c="dimmed">
                {playerFacts(player)}
              </Text>
            </div>
            {error && <Alert color="red">{error}</Alert>}
            <Group gap={8}>
              <Button loading={state.matches({ turn: "submitting" })} onClick={() => send({ type: "CONFIRM" })}>
                Draft {player.name}
              </Button>
              <Button
                variant="default"
                disabled={state.matches({ turn: "submitting" })}
                onClick={() => send({ type: "BACK" })}
              >
                Back
              </Button>
            </Group>
          </Stack>
        ) : (
          <>
            {reasons.length > 0 && <Alert color="red">{reasons.join(" ")}</Alert>}
            <PlayerSearch onSelect={(selected) => send({ type: "SELECT", player: selected })} />
          </>
        )}
      </Stack>
    </Card>
  );
}

function PlayerSearch({ onSelect }: { onSelect: (player: DraftPlayer) => void }) {
  const [search, setSearch] = useState("");
  const [q] = useDebouncedValue(search, 300);
  const { data: players = [], error } = useQuery(eligiblePlayers(q));
  return (
    <Select
      label="Find a player"
      placeholder="Search by name"
      searchable
      searchValue={search}
      onSearchChange={setSearch}
      filter={({ options }) => options} // the server already searched
      data={players.map((p) => ({ value: p.fantrax_id, label: `${p.name} · ${playerFacts(p)}` }))}
      value={null}
      onChange={(id) => {
        const player = players.find((p) => p.fantrax_id === id);
        if (player) onSelect(player);
      }}
      nothingFoundMessage={error ? `Couldn't search. ${error.message}` : "No eligible player matches"}
    />
  );
}
