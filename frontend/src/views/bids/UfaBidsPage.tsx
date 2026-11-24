import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  List,
  Modal,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDebouncedValue, useDisclosure } from "@mantine/hooks";
import { useMutation, useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { type ApiError, Blocked } from "../../api/errors";
import type { UfaBid, UfaBids } from "../../api/generated";
import { openPlayer } from "../../bridge";
import { fmtMoney } from "../../format";
import { me } from "../../queries/me";
import { teams } from "../../queries/teams";
import { bidPreview, freeAgents, makeBid, ufaBids, voidBid, withdrawBid } from "../../queries/ufaBids";

const RESULTS: Record<string, string> = {
  signed: "Signed",
  outbid: "Outbid",
  withdrawn: "Withdrawn",
  cancelled: "Cancelled: a team added him in Fantrax",
  voided: "Void",
};

const message = (error: ApiError) => (error instanceof Blocked ? error.reasons.join(" ") : error.message);
const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

// From the server's clock when the board loaded, so a wrong clock on this computer can't change it.
function timeLeft(deadline: string, now: string): string {
  const minutes = Math.max(0, Math.round((Date.parse(deadline) - Date.parse(now)) / 60_000));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function inFantrax(bid: UfaBid): ReactNode {
  if (bid.on_team_id === null) return "Waiting for the add in Fantrax";
  if (bid.on_team_id === bid.team_id) return "Added: the next sync signs him";
  return (
    <Text span inherit c="red">
      Another team has him in Fantrax
    </Text>
  );
}

// A raise starts at the lowest bid that beats the high bid.
type Start = { player?: { id: string; name: string }; salary?: number };

export function UfaBidsPage() {
  const { data, error } = useQuery(ufaBids);
  const admin = Boolean(useQuery(me).data?.is_admin);
  const [start, setStart] = useState<Start | null>(null);
  if (error) return <Text size="sm">Couldn't load the bids. {error.message}</Text>;
  if (!data) return null;
  const canBid = Boolean(data.team_id) || admin;
  const of = (states: string[]) => data.bids.filter((b) => states.includes(b.state));
  return (
    <Stack gap={16}>
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={2}>In-season UFA bids</Title>
          <Text size="xs" c="dimmed">
            The high bid wins a player on no CFL team when its time runs out. The winner adds him in Fantrax, and the
            next sync gives him a 1-year contract at the bid.
          </Text>
        </Stack>
        {canBid && <Button onClick={() => setStart({})}>New bid</Button>}
      </Group>
      <Section title="Open bids" bids={of(["pending"])} head={["High bid", "Team", "Time left"]}>
        {(bid) => (
          <>
            <Table.Td>{timeLeft(bid.deadline, data.now)}</Table.Td>
            <Table.Td>
              <Group gap={6} justify="flex-end" wrap="nowrap">
                {canBid && bid.team_id !== data.team_id && (
                  <Button
                    size="xs"
                    variant="default"
                    onClick={() =>
                      setStart({
                        player: { id: bid.player_id, name: bid.player_name },
                        salary: bid.salary + data.min_raise,
                      })
                    }
                  >
                    Raise
                  </Button>
                )}
                {data.can_withdraw && (admin || bid.team_id === data.team_id) && <WithdrawButton bid={bid} />}
                {admin && <VoidButton bid={bid} />}
              </Group>
            </Table.Td>
          </>
        )}
      </Section>
      <Section title="Won: add in Fantrax" bids={of(["won"])} head={["Bid", "Team", "Fantrax"]}>
        {(bid) => (
          <>
            <Table.Td>{inFantrax(bid)}</Table.Td>
            <Table.Td>{admin && <VoidButton bid={bid} />}</Table.Td>
          </>
        )}
      </Section>
      {admin && <UnbidPickups board={data} />}
      <Section title="Earlier this season" bids={of(Object.keys(RESULTS))} head={["Bid", "Team", "Result", "Bid on"]}>
        {(bid) => (
          <>
            <Table.Td>{RESULTS[bid.state]}</Table.Td>
            <Table.Td>{day(bid.created_at)}</Table.Td>
          </>
        )}
      </Section>
      <Modal
        opened={start !== null}
        onClose={() => setStart(null)}
        title={start?.player ? `Bid on ${start.player.name}` : "New bid"}
      >
        {start && <BidForm start={start} board={data} admin={admin} onDone={() => setStart(null)} />}
      </Modal>
    </Stack>
  );
}

function Section({
  title,
  bids,
  head,
  children,
}: {
  title: string;
  bids: UfaBid[];
  head: string[];
  children: (bid: UfaBid) => ReactNode;
}) {
  return (
    <Card component="section">
      <Title order={3} mb={8}>
        {title}
      </Title>
      {bids.length ? (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Player</Table.Th>
              {head.map((h) => (
                <Table.Th key={h}>{h}</Table.Th>
              ))}
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {bids.map((bid) => (
              <Table.Tr key={bid.id}>
                <Table.Td>
                  {bid.player_name}
                  <Text size="xs" c="dimmed">
                    {[bid.position, bid.nhl_team].filter(Boolean).join(" · ")}
                  </Text>
                </Table.Td>
                <Table.Td>{fmtMoney(bid.salary)}</Table.Td>
                <Table.Td>{bid.team_name}</Table.Td>
                {children(bid)}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <Text size="sm" c="dimmed">
          None.
        </Text>
      )}
    </Card>
  );
}

function UnbidPickups({ board }: { board: UfaBids }) {
  return (
    <Card component="section">
      <Title order={3}>Pickups with no winning bid</Title>
      <Text size="xs" c="dimmed" mb={8}>
        Each has a $1 placeholder contract. Set his salary, or have the team drop him in Fantrax.
      </Text>
      {board.unbid_pickups.length ? (
        <List listStyleType="none" spacing={4}>
          {board.unbid_pickups.map((p) => (
            <List.Item key={p.player_id}>
              <Anchor component="button" onClick={() => openPlayer(p.player_id)}>
                {p.player_name}
              </Anchor>{" "}
              · {p.team_name}
            </List.Item>
          ))}
        </List>
      ) : (
        <Text size="sm" c="dimmed">
          None.
        </Text>
      )}
    </Card>
  );
}

function WithdrawButton({ bid }: { bid: UfaBid }) {
  const withdraw = useMutation(withdrawBid);
  return (
    <>
      {withdraw.error && (
        <Text size="xs" c="red">
          {message(withdraw.error)}
        </Text>
      )}
      <Button size="xs" variant="default" loading={withdraw.isPending} onClick={() => withdraw.mutate(bid.id)}>
        Withdraw
      </Button>
    </>
  );
}

function VoidButton({ bid }: { bid: UfaBid }) {
  const [opened, { open, close }] = useDisclosure();
  const [reason, setReason] = useState("");
  const voiding = useMutation(voidBid);
  return (
    <>
      <Button size="xs" variant="default" onClick={open}>
        Void
      </Button>
      <Modal opened={opened} onClose={close} title={`Void ${bid.team_name}'s bid on ${bid.player_name}`}>
        <Stack
          component="form"
          onSubmit={(event) => {
            event.preventDefault();
            voiding.mutate({ id: bid.id, reason }, { onSuccess: close });
          }}
        >
          <TextInput
            label="Why"
            placeholder="Bid on the wrong player"
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value)}
          />
          {voiding.error && <Alert color="red">{message(voiding.error)}</Alert>}
          <Group justify="flex-end">
            <Button type="submit" loading={voiding.isPending}>
              Void bid
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

function BidForm({
  start,
  board,
  admin,
  onDone,
}: {
  start: Start;
  board: UfaBids;
  admin: boolean;
  onDone: () => void;
}) {
  const [player, setPlayer] = useState(start.player ?? null);
  const [search, setSearch] = useState("");
  const [salary, setSalary] = useState<string | number>(start.salary ?? "");
  const [teamId, setTeamId] = useState(board.team_id);
  const [force, setForce] = useState("");
  const [typedSearch] = useDebouncedValue(search, 300);
  const [typedSalary] = useDebouncedValue(Number(salary), 300);
  const found = useQuery(freeAgents(typedSearch)).data ?? [];
  const leagueTeams = useQuery({ ...teams, enabled: admin }).data ?? [];
  const params = { player_id: player?.id ?? "", salary: typedSalary, team_id: teamId ?? "" };
  const preview = useQuery({ ...bidPreview(params), enabled: Boolean(player && teamId) && typedSalary > 0 });
  const bid = useMutation(makeBid);
  const reasons = preview.data?.reasons ?? [];
  const ready = Boolean(player && teamId && preview.data) && (!reasons.length || (admin && force.trim()));
  return (
    <Stack
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!player || !teamId) return;
        bid.mutate(
          { player_id: player.id, salary: Number(salary), team_id: teamId, force_reason: force.trim() || null },
          { onSuccess: onDone, onError: () => preview.refetch() },
        );
      }}
    >
      {admin && (
        <Select
          label="For"
          placeholder="Pick a team"
          data={leagueTeams.map((t) => ({ value: t.id, label: t.name }))}
          value={teamId}
          onChange={setTeamId}
          allowDeselect={false}
        />
      )}
      {!start.player && (
        <Select
          label="Player"
          placeholder="Type 2 letters or more"
          searchable
          searchValue={search}
          onSearchChange={setSearch}
          // The API searched already, by name.
          filter={({ options }) => options}
          data={found.map((p) => ({ value: p.player_id, label: p.name }))}
          value={player?.id ?? null}
          onChange={(id, option) => setPlayer(id && option ? { id, name: option.label } : null)}
          nothingFoundMessage={typedSearch.length >= 2 ? "No free agent by that name" : undefined}
        />
      )}
      <NumberInput
        label="Bid"
        prefix="$"
        min={0}
        decimalScale={1}
        value={salary}
        onChange={setSalary}
        description={preview.data && `At least ${fmtMoney(preview.data.min_salary)}`}
      />
      {reasons.length > 0 && (
        <Alert color="yellow" title="The rules refuse this bid">
          <List size="sm">
            {reasons.map((r) => (
              <List.Item key={r}>{r}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      {preview.data && !reasons.length && (
        <Text size="sm">Cap space after this bid: {fmtMoney(preview.data.cap_space)}</Text>
      )}
      {admin && reasons.length > 0 && (
        <TextInput
          label="Reason to allow it"
          placeholder="Replaces an injured player"
          value={force}
          onChange={(event) => setForce(event.currentTarget.value)}
        />
      )}
      {bid.error && <Alert color="red">{message(bid.error)}</Alert>}
      <Group justify="flex-end">
        <Button type="submit" disabled={!ready} loading={bid.isPending}>
          Bid
        </Button>
      </Group>
    </Stack>
  );
}
