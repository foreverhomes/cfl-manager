import { MantineProvider } from "@mantine/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { queryClient } from "../queries/client";
import { cssVariables, legacyColorScheme, theme } from "./theme";

export function Providers({ children }: { children: ReactNode }) {
  const [colorScheme] = useState(legacyColorScheme);
  return (
    <MantineProvider theme={theme} cssVariablesResolver={cssVariables} colorSchemeManager={colorScheme}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MantineProvider>
  );
}
