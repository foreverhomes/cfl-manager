import { queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";

export const me = queryOptions({ queryKey: ["me"], queryFn: ({ signal }) => run(cfl.authMe(undefined), signal) });
