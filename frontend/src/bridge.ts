import { Unauthorized } from "./api/errors";
import { queryClient } from "./queries/client";

const listeners = new Set<() => void>();
const signedOutListeners = new Set<() => void>();

export function onDataChanged(listener: () => void) {
  listeners.add(listener);
}

export function dataChanged() {
  queryClient.invalidateQueries();
  for (const listener of listeners) listener();
}

export function onSignedOut(listener: () => void) {
  signedOutListeners.add(listener);
}

queryClient.getQueryCache().subscribe((event) => {
  if (event.type === "updated" && event.action.type === "error" && event.action.error instanceof Unauthorized) {
    for (const listener of signedOutListeners) listener();
  }
});

let playerDialog: (id: string) => void = () => {};

// The legacy contract dialog, which React views open until Players & cap moves to React.
export function onOpenPlayer(open: (id: string) => void) {
  playerDialog = open;
}

export function openPlayer(id: string) {
  playerDialog(id);
}
