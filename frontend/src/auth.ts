import { cfl, run } from "./api/client";

declare global {
  interface Window {
    google?: { accounts: { id: { disableAutoSelect(): void } } };
  }
}

export async function signOut() {
  try {
    await run(cfl.authLogout(undefined));
  } catch {
    // signed out either way
  }
  window.google?.accounts.id.disableAutoSelect();
  window.location.hash = "";
  window.location.reload();
}
