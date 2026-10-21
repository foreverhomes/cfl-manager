import { afterEach, expect, it, vi } from "vitest";
import { Forbidden, Unauthorized } from "./api/errors";
import { dataChanged, onDataChanged, onSignedOut } from "./bridge";
import { queryClient } from "./queries/client";

afterEach(() => queryClient.clear());

const failWith = (error: Error) =>
  queryClient.fetchQuery({ queryKey: [crypto.randomUUID()], queryFn: () => Promise.reject(error) }).catch(() => {});

it("marks every cached query stale", () => {
  queryClient.setQueryData(["teams"], []);
  dataChanged();
  expect(queryClient.getQueryState(["teams"])?.isInvalidated).toBe(true);
});

it("calls each listener", () => {
  const listener = vi.fn();
  onDataChanged(listener);
  dataChanged();
  expect(listener).toHaveBeenCalledOnce();
});

it("calls each signed-out listener when a query gets Unauthorized", async () => {
  const listener = vi.fn();
  onSignedOut(listener);
  await failWith(new Unauthorized());
  expect(listener).toHaveBeenCalledOnce();
});

it("doesn't call the signed-out listeners for other errors", async () => {
  const listener = vi.fn();
  onSignedOut(listener);
  await failWith(new Forbidden({ message: "No" }));
  expect(listener).not.toHaveBeenCalled();
});
