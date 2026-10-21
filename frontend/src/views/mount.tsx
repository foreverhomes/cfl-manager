import type { ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { Providers } from "./Providers";

const mounted = new WeakSet<Element>();

export function mountView(element: Element, View: ComponentType) {
  if (mounted.has(element)) return;
  mounted.add(element);
  createRoot(element).render(
    <Providers>
      <View />
    </Providers>,
  );
}
