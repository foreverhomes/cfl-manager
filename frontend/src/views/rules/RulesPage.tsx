import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Group,
  List,
  Modal,
  NumberInput,
  type NumberInputProps,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import { useDisclosure } from "@mantine/hooks";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { LeagueRule } from "../../api/generated";
import { fmtMoney, fmtMonthDay } from "../../format";
import { changeRule, rules } from "../../queries/rules";

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
  if (kind === "share") return `${Math.round(Number(value) * 100)}%`;
  if (kind === "month_day") return fmtMonthDay(String(value));
  return String(value);
}

// How a box shows each kind. The API checks the value, and a day of the year comes from a calendar.
const INPUTS: Record<string, NumberInputProps> = {
  money: { prefix: "$", min: 0, decimalScale: 1 },
  count: { min: 1, allowDecimal: false },
  share: { suffix: "%", min: 1, max: 100, allowDecimal: false },
};

// The value as the form shows it: a share as a whole percent, and the cap's null as an empty box.
function typed(kind: string, value: LeagueRule["value"]): string | number {
  if (kind === "share") return Math.round(Number(value) * 100);
  return typeof value === "number" || typeof value === "string" ? value : "";
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
        <Stack component="section" key={prefix} gap={4} mt={16}>
          <Title order={3}>{title}</Title>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Rule</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Changes</Table.Th>
                {data.can_edit && <Table.Th />}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.rules
                .filter((rule) => rule.key.startsWith(`${prefix}.`))
                .map((rule) => (
                  <RuleRow key={rule.key} rule={rule} canEdit={data.can_edit} leagueYear={data.league_year} />
                ))}
            </Table.Tbody>
          </Table>
        </Stack>
      ))}
    </>
  );
}

function RuleRow({ rule, canEdit, leagueYear }: { rule: LeagueRule; canEdit: boolean; leagueYear: number }) {
  const [editing, { open, close }] = useDisclosure();
  return (
    <Table.Tr>
      <Table.Td>
        {rule.label}
        <Text size="xs" c="dimmed">
          Rules:{" "}
          <Anchor href={`${WRITTEN_RULES}#${anchor(rule.cite)}`} inherit>
            {rule.cite}
          </Anchor>
        </Text>
      </Table.Td>
      <Table.Td style={{ whiteSpace: "nowrap" }}>
        {show(rule.kind, rule.value)}
        {rule.value !== rule.default && (
          <Text size="xs" c="dimmed">
            Default {show(rule.kind, rule.default)}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {rule.changes.length ? (
          <List listStyleType="none" spacing={4}>
            {rule.changes.map((c) => (
              <List.Item key={c.changed_at + c.from_year}>
                {c.value === null ? "The default" : show(rule.kind, c.value)} from {season(c.from_year)}
                {c.note && `: ${c.note}`}
                <Text span inherit c="dimmed">
                  {" "}
                  · {c.changed_by}, {day(c.changed_at)}
                </Text>
              </List.Item>
            ))}
          </List>
        ) : (
          <Text span inherit c="dimmed">
            None
          </Text>
        )}
      </Table.Td>
      {canEdit && (
        <Table.Td>
          <Button size="xs" variant="default" onClick={open}>
            Change
          </Button>
          <Modal opened={editing} onClose={close} title={rule.label}>
            <RuleForm rule={rule} leagueYear={leagueYear} onSaved={close} />
          </Modal>
        </Table.Td>
      )}
    </Table.Tr>
  );
}

function RuleForm({ rule, leagueYear, onSaved }: { rule: LeagueRule; leagueYear: number; onSaved: () => void }) {
  const [value, setValue] = useState(typed(rule.kind, rule.value));
  const [from, setFrom] = useState<string | null>(String(leagueYear));
  const [note, setNote] = useState("");
  const [toDefault, setToDefault] = useState(false);
  const save = useMutation(changeRule(rule.key));
  const seasons = Array.from({ length: 5 }, (_, i) => ({
    value: String(leagueYear + i),
    label: season(leagueYear + i),
  }));
  const box = { label: "Value", value, disabled: toDefault };
  return (
    <Stack
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        const sent = rule.kind === "share" ? Number(value) / 100 : value;
        save.mutate({ value: toDefault ? null : sent, from_year: Number(from), note }, { onSuccess: onSaved });
      }}
    >
      {rule.kind === "month_day" ? (
        // The rule has no year, so the calendar stays in 2001: no Feb 29, as in the API's check.
        <DatePickerInput
          {...box}
          value={`2001-${value}`}
          onChange={(date) => date && setValue(date.slice(5))}
          valueFormat="MMMM D"
          monthLabelFormat="MMMM"
          maxLevel="month"
          hideWeekdays
          getDayAriaLabel={(date) => show(rule.kind, date.slice(5))}
          minDate="2001-01-01"
          maxDate="2001-12-31"
        />
      ) : (
        <NumberInput {...box} {...INPUTS[rule.kind]} onChange={setValue} />
      )}
      <Checkbox
        label={`Use the default: ${show(rule.kind, rule.default)}`}
        checked={toDefault}
        onChange={(event) => setToDefault(event.currentTarget.checked)}
      />
      <Select label="Starts in" data={seasons} value={from} onChange={setFrom} allowDeselect={false} />
      <TextInput
        label="Why"
        placeholder="2027 league vote"
        value={note}
        onChange={(event) => setNote(event.currentTarget.value)}
      />
      {save.error && <Alert color="red">{save.error.message}</Alert>}
      <Group justify="flex-end">
        <Button type="submit" loading={save.isPending}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}
