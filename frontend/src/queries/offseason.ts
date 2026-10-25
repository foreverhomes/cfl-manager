import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";

// null: the next offseason. The last list stays on screen while another year loads.
export const expiringContracts = (year: number | null) =>
  queryOptions({
    queryKey: ["offseason", year],
    queryFn: ({ signal }) => run(cfl.offseason({ params: { year: year ?? undefined } }), signal),
    placeholderData: keepPreviousData,
  });
