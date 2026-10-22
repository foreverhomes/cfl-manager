import { queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";

export const rules = queryOptions({ queryKey: ["rules"], queryFn: ({ signal }) => run(cfl.rules(undefined), signal) });
