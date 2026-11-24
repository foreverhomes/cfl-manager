import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";
import type { NewUfaBid, UfaBids } from "../api/generated";
import { queryClient } from "./client";

// Again every minute, so a bid shows as won soon after its time runs out.
export const ufaBids = queryOptions({
  queryKey: ["ufa-bids"],
  queryFn: ({ signal }) => run(cfl.ufaBids(undefined), signal),
  refetchInterval: 60_000,
});

export const freeAgents = (q: string) =>
  queryOptions({
    queryKey: ["free-agents", q],
    queryFn: ({ signal }) => run(cfl.freeAgents({ params: { q } }), signal),
    enabled: q.length >= 2,
  });

export const bidPreview = (params: { player_id: string; salary: number; team_id?: string }) =>
  queryOptions({
    queryKey: ["ufa-bid-preview", params],
    queryFn: ({ signal }) => run(cfl.previewUfaBid({ params }), signal),
  });

// Each change answers with the whole board.
const showBoard = (board: UfaBids) => queryClient.setQueryData(ufaBids.queryKey, board);

export const makeBid = mutationOptions({
  mutationFn: (bid: NewUfaBid) => run(cfl.makeUfaBid({ payload: bid })),
  onSuccess: showBoard,
});

export const withdrawBid = mutationOptions({
  mutationFn: (id: string) => run(cfl.withdrawUfaBid(id, undefined)),
  onSuccess: showBoard,
});

export const voidBid = mutationOptions({
  mutationFn: ({ id, reason }: { id: string; reason: string }) => run(cfl.voidUfaBid(id, { payload: { reason } })),
  onSuccess: showBoard,
});
