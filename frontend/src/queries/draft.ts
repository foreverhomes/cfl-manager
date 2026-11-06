import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";
import { queryClient } from "./client";

export const draftBoard = queryOptions({
  queryKey: ["draft"],
  queryFn: ({ signal }) => run(cfl.draft(undefined), signal),
  refetchInterval: 30_000,
});

// The last list stays on screen while the next search loads.
export const eligiblePlayers = (q: string) =>
  queryOptions({
    queryKey: ["draft", "eligible", q],
    queryFn: ({ signal }) => run(cfl.draftEligible({ params: { q } }), signal),
    placeholderData: keepPreviousData,
  });

export const refreshDraft = () => queryClient.invalidateQueries({ queryKey: draftBoard.queryKey });
