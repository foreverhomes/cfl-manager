import { QueryClient } from "@tanstack/react-query";
import { type ApiError, Failed } from "../api/errors";

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiError;
  }
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: (failures, error) => error instanceof Failed && error.status === 0 && failures < 3 },
  },
});
