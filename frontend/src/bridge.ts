import { queryClient } from "./queries/client";

const listeners = new Set<() => void>();

export function onDataChanged(listener: () => void) {
  listeners.add(listener);
}

export function dataChanged() {
  queryClient.invalidateQueries();
  for (const listener of listeners) listener();
}
