// @vitest-environment jsdom
import { useQueryClient } from "@tanstack/react-query";
import { act, within } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import { queryClient } from "../queries/client";
import { mountView } from "./mount";

it("renders the view inside the shared QueryClientProvider", async () => {
  const element = document.createElement("div");
  function Probe() {
    return <p>{useQueryClient() === queryClient ? "shared client" : "other client"}</p>;
  }
  await act(async () => mountView(element, Probe));
  expect(within(element).getByText("shared client")).toBeTruthy();
});

it("keeps the view's state when its tab shows again", async () => {
  const element = document.createElement("div");
  let mounts = 0;
  function Counter() {
    const [mount] = useState(() => ++mounts);
    return <p>mount {mount}</p>;
  }
  await act(async () => mountView(element, Counter));
  await act(async () => mountView(element, Counter));
  expect(within(element).getByText("mount 1")).toBeTruthy();
});
