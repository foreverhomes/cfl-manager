// Each component's styles, in Mantine's CSS layer so that styles.css wins any conflict. Not
// @mantine/core/styles.css: its baseline restyles <body> and every element of the legacy page.
// The default variables come first: MantineProvider adds only the ones the theme changes.
import "@mantine/core/styles/default-css-variables.layer.css";
import "@mantine/core/styles/UnstyledButton.layer.css";
import "@mantine/core/styles/Button.layer.css";
import "@mantine/core/styles/Avatar.layer.css";
import "@mantine/core/styles/Text.layer.css";
import "@mantine/core/styles/Title.layer.css";
import "@mantine/core/styles/Group.layer.css";
import "@mantine/core/styles/Stack.layer.css";
import "@mantine/core/styles/Anchor.layer.css";
import {
  Avatar,
  type CSSVariablesResolver,
  createTheme,
  type MantineColorScheme,
  type MantineColorSchemeManager,
} from "@mantine/core";

export const theme = createTheme({
  fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  fontSizes: { xs: "12px", sm: "13px", md: "14px", lg: "16px", xl: "18px" },
  lineHeights: { md: "1.45" },
  headings: { sizes: { h2: { fontSize: "15px", lineHeight: "1.3" } } },
  radius: { sm: "8px", md: "12px" },
  defaultRadius: "sm",
  components: {
    Avatar: Avatar.extend({
      vars: () => ({ root: { "--avatar-bg": "var(--ink)", "--avatar-color": "var(--surface)" } }),
    }),
  },
});

// styles.css's tokens already switch between light and dark, so both schemes point at them.
const tokens = {
  "--mantine-color-body": "var(--surface)",
  "--mantine-color-text": "var(--ink)",
  "--mantine-color-dimmed": "var(--muted)",
  "--mantine-color-default": "transparent",
  "--mantine-color-default-hover": "var(--wash)",
  "--mantine-color-default-color": "var(--ink-2)",
  "--mantine-color-default-border": "var(--border)",
  "--mantine-primary-color-filled": "var(--accent)",
  "--mantine-primary-color-contrast": "var(--accent-ink)",
};
export const cssVariables: CSSVariablesResolver = () => ({ variables: {}, light: tokens, dark: tokens });

// The legacy Account page owns the choice: <html data-theme="light|dark">, or none for the device's.
const scheme = (): MantineColorScheme => {
  const chosen = document.documentElement.dataset.theme;
  return chosen === "light" || chosen === "dark" ? chosen : "auto";
};

export function legacyColorScheme(): MantineColorSchemeManager {
  const observer = new MutationObserver(() => onChange?.(scheme()));
  let onChange: ((colorScheme: MantineColorScheme) => void) | undefined;
  return {
    get: scheme,
    set: () => {},
    subscribe: (onUpdate) => {
      onChange = onUpdate;
      observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    },
    unsubscribe: () => observer.disconnect(),
    clear: () => {},
  };
}
