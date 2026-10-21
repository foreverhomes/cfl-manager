// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import type { Me, Team } from "../../api/generated";
import { queryClient } from "../../queries/client";
import { AccountCard } from "./AccountCard";

const teams: ReadonlyArray<Team> = [
  {
    id: "t1",
    name: "Lindy Ruffryders",
    short_name: "LR",
    rank: 2,
    record: "1-0-0",
    games_back: 0,
    win_pct: 1,
    is_me: 1,
  },
];

const server = setupServer(http.get("*/teams", () => HttpResponse.json(teams)));
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  queryClient.clear();
});
afterAll(() => server.close());

const showCard = (me: Me) => {
  server.use(http.get("*/auth/me", () => HttpResponse.json(me)));
  render(
    <QueryClientProvider client={queryClient}>
      <AccountCard />
    </QueryClientProvider>,
  );
};

it("shows a GM who they are, their team and their role", async () => {
  showCard({
    email: "gm@example.com",
    name: "Pat Quinn",
    team_id: "t1",
    is_admin: false,
    status: "active",
    auth: true,
  });
  expect(await screen.findByText("Pat Quinn")).toBeTruthy();
  expect(screen.getByText("gm@example.com")).toBeTruthy();
  expect(await screen.findByText("Lindy Ruffryders")).toBeTruthy();
  expect(screen.getByText("GM")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
});

it("says there's nothing to sign out of without logins", async () => {
  showCard({ email: "local", name: null, team_id: null, is_admin: true, status: "active", auth: false });
  expect(await screen.findByText("Admin")).toBeTruthy();
  expect(screen.getByText("Not linked yet")).toBeTruthy();
  expect(screen.getByText(/nothing to sign out of/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
});
