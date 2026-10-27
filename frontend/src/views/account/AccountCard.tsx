import { Avatar, Button, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { signOut } from "../../auth";
import { me } from "../../queries/me";
import { teams } from "../../queries/teams";

export function AccountCard() {
  const { data: user } = useQuery(me);
  const { data: teamList } = useQuery(teams);
  if (!user) return null;
  const team = user.team_id ? (teamList?.find((t) => t.id === user.team_id)?.name ?? user.team_id) : "Not linked yet";
  return (
    <>
      <Stack gap={2} mb={12}>
        <Title order={2}>Your account</Title>
        <Text size="xs" c="dimmed">
          {user.auth ? "Signed in with Google" : "Logins aren't set up on this computer"}
        </Text>
      </Stack>
      {user.auth && (
        <Group gap={12} pb={14} mb={14} style={{ borderBottom: "1px solid var(--grid)" }}>
          <Avatar size={44} radius="xl">
            {(user.name || user.email).trim().charAt(0).toUpperCase()}
          </Avatar>
          <div>
            <Text size="lg" fw={600}>
              {user.name || user.email}
            </Text>
            {user.name && (
              <Text size="xs" c="dimmed">
                {user.email}
              </Text>
            )}
          </div>
        </Group>
      )}
      <Table variant="vertical" mb={18}>
        <Table.Tbody>
          <Table.Tr>
            <Table.Th>Team</Table.Th>
            <Table.Td>{team}</Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Th>Role</Table.Th>
            <Table.Td>{user.is_admin ? "Admin" : "GM"}</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      {user.auth ? (
        <Button variant="default" onClick={signOut}>
          Sign out
        </Button>
      ) : (
        <Text size="xs" c="dimmed">
          Without GOOGLE_CLIENT_ID, everyone who opens the app is the admin, so there's nothing to sign out of.
        </Text>
      )}
    </>
  );
}
