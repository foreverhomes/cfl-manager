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
import "@mantine/core/styles/Table.layer.css";
import "@mantine/core/styles/List.layer.css";
import "@mantine/core/styles/Paper.layer.css";
import "@mantine/core/styles/CloseButton.layer.css";
import "@mantine/core/styles/Overlay.layer.css";
import "@mantine/core/styles/ModalBase.layer.css";
import "@mantine/core/styles/Modal.layer.css";
import "@mantine/core/styles/Input.layer.css";
import "@mantine/core/styles/NumberInput.layer.css";
import "@mantine/core/styles/Popover.layer.css";
import "@mantine/core/styles/Combobox.layer.css";
import "@mantine/core/styles/ScrollArea.layer.css";
import "@mantine/core/styles/InlineInput.layer.css";
import "@mantine/core/styles/Checkbox.layer.css";
import "@mantine/core/styles/Alert.layer.css";
import "@mantine/core/styles/Loader.layer.css";
import "@mantine/dates/styles.layer.css";
import {
  Avatar,
  Checkbox,
  type CSSVariablesResolver,
  createTheme,
  Input,
  type MantineColorScheme,
  type MantineColorSchemeManager,
  Popover,
  Table,
} from "@mantine/core";

// The shades Mantine draws an input's box and a dropdown with, in light and dark. There only, they're the page's.
const box = {
  "--mantine-color-white": "var(--surface)",
  "--mantine-color-gray-4": "var(--border)",
  "--mantine-color-dark-6": "var(--surface)",
  "--mantine-color-dark-4": "var(--border)",
};

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
    // Like the page's .data-table: 14px text, 6px by 8px cells, --grid between rows, --axis under the header.
    Table: Table.extend({
      vars: () => ({
        table: {
          "--table-border-color": "var(--grid)",
          "--table-vertical-spacing": "6px",
          "--table-horizontal-spacing": "8px",
        },
      }),
      styles: {
        table: { fontSize: "var(--mantine-font-size-md)" },
        th: {
          color: "var(--muted)",
          fontSize: "12px",
          fontWeight: 500,
          whiteSpace: "nowrap",
          borderBottom: "1px solid var(--axis)",
        },
      },
    }),
    Input: Input.extend({ styles: { wrapper: box } }),
    Checkbox: Checkbox.extend({ defaultProps: { radius: 4 }, styles: { input: box } }),
    Popover: Popover.extend({ styles: { dropdown: box } }),
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
  "--mantine-color-red-light": "color-mix(in oklab, var(--bad) 7%, var(--surface))",
  "--mantine-color-red-light-color": "var(--bad)",
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
