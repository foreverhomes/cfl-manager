import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { cfl, run } from "../api/client";
import type { RuleEdit } from "../api/generated";
import { dataChanged } from "../bridge";

export const rules = queryOptions({ queryKey: ["rules"], queryFn: ({ signal }) => run(cfl.rules(undefined), signal) });

// A rule can change the cap or the draft's rounds, so every view loads again, the legacy ones too.
export const changeRule = (key: string) =>
  mutationOptions({
    mutationFn: (edit: RuleEdit) => run(cfl.changeRule(key, { payload: edit })),
    onSuccess: dataChanged,
  });
