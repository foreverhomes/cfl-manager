import { queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";

export const teams = queryOptions({ queryKey: ["teams"], queryFn: ({ signal }) => run(cfl.teams(undefined), signal) });
