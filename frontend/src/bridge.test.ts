import { afterEach, expect, it, vi } from "vitest";
import { dataChanged, onDataChanged } from "./bridge";
import { queryClient } from "./queries/client";

afterEach(() => queryClient.clear());

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
