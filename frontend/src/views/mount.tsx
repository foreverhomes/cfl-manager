import { QueryClientProvider } from "@tanstack/react-query";
import type { ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { queryClient } from "../queries/client";

const mounted = new WeakSet<Element>();

export function mountView(element: Element, View: ComponentType) {
  if (mounted.has(element)) return;
  mounted.add(element);
  createRoot(element).render(
    <QueryClientProvider client={queryClient}>
      <View />
    </QueryClientProvider>,
  );
}
