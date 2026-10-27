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
import "@mantine/core/styles/Card.layer.css";
import "@mantine/core/styles/SimpleGrid.layer.css";
import "@mantine/core/styles/SegmentedControl.layer.css";
import "@mantine/core/styles/Badge.layer.css";
import "@mantine/core/styles/Tooltip.layer.css";
import "@mantine/core/styles/Accordion.layer.css";
import "@mantine/dates/styles.layer.css";
import type {
  AccordionFactory,
  AvatarFactory,
  BadgeFactory,
  CardFactory,
  CheckboxFactory,
  CSSVariablesResolver,
  ExtendComponent,
  InputFactory,
  InputWrapperFactory,
  MantineColorScheme,
  MantineColorSchemeManager,
  PopoverFactory,
  SegmentedControlFactory,
  TableFactory,
  TooltipFactory,
} from "@mantine/core";
import { createTheme } from "@mantine/core";

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
    Avatar: {
      vars: () => ({ root: { "--avatar-bg": "var(--ink)", "--avatar-color": "var(--surface)" } }),
    } satisfies ExtendComponent<AvatarFactory>,
    // Like the page's .data-table: 14px text, 6px by 8px cells, --grid between rows, --axis under the header.
    // variant="vertical" labels facts instead: no lines, 6px between rows, each label as wide as the longest.
    Table: {
      vars: (_, { variant }) => ({
        table: {
          "--table-border-color": "var(--grid)",
          "--table-vertical-spacing": variant === "vertical" ? "3px" : "6px",
          "--table-horizontal-spacing": "8px",
        },
      }),
      styles: (_, { variant }) => ({
        table: { fontSize: "var(--mantine-font-size-md)" },
        th: {
          whiteSpace: "nowrap",
          ...(variant === "vertical"
            ? { width: "1%", padding: "3px 24px 3px 0", background: "none", color: "var(--ink-2)", fontWeight: 400 }
            : { color: "var(--muted)", fontSize: "12px", fontWeight: 500, borderBottom: "1px solid var(--axis)" }),
        },
        tr: variant === "vertical" ? { borderBottom: 0 } : {},
        td: variant === "vertical" ? { paddingLeft: 0, fontWeight: 500 } : {},
      }),
    } satisfies ExtendComponent<TableFactory>,
    Input: { styles: { wrapper: box } } satisfies ExtendComponent<InputFactory>,
    // Like the page's .field-label.
    InputWrapper: {
      styles: {
        label: { display: "block", fontSize: "12px", fontWeight: 400, color: "var(--ink-2)", marginBottom: "4px" },
      },
    } satisfies ExtendComponent<InputWrapperFactory>,
    // Like the page's .card and .tile.
    Card: {
      defaultProps: { withBorder: true, padding: 16 },
      styles: {
        root: {
          background: "var(--surface)",
          borderColor: "var(--border)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow)",
        },
      },
    } satisfies ExtendComponent<CardFactory>,
    // Like the page's .segmented: the chosen option in --ink.
    SegmentedControl: {
      vars: () => ({
        root: {
          "--sc-color": "var(--ink)",
          "--sc-label-color": "var(--surface)",
          "--sc-radius": "8px",
          "--sc-padding": "7px 12px",
          "--sc-font-size": "var(--mantine-font-size-md)",
        },
      }),
      styles: {
        root: {
          "--mantine-color-gray-7": "var(--ink-2)",
          "--mantine-color-dark-1": "var(--ink-2)",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          padding: 0,
        },
      },
    } satisfies ExtendComponent<SegmentedControlFactory>,
    // Like the page's .pill: a dot, a border and a wash of the color, which is any CSS color.
    Badge: {
      defaultProps: { variant: "dot" },
      vars: (_, { color }) => ({
        root: {
          "--badge-dot-color": color,
          "--badge-bg": `color-mix(in oklab, ${color} 14%, transparent)`,
          "--badge-bd": `1px solid color-mix(in oklab, ${color} 55%, transparent)`,
          "--badge-color": "var(--ink)",
          "--badge-fz": "11px",
          "--badge-height": "18px",
          "--badge-padding-x": "7px",
          "--badge-radius": "999px",
        },
      }),
      styles: { root: { textTransform: "none", fontWeight: 400, verticalAlign: "1px" } },
    } satisfies ExtendComponent<BadgeFactory>,
    // Like the page's .tooltip.
    Tooltip: {
      defaultProps: { multiline: true, w: 260 },
      vars: () => ({
        tooltip: { "--tooltip-bg": "var(--surface)", "--tooltip-color": "var(--ink)", "--tooltip-radius": "8px" },
      }),
      styles: {
        tooltip: {
          border: "1px solid var(--border)",
          padding: "8px 10px",
          fontSize: "12px",
          boxShadow: "0 4px 16px rgba(0, 0, 0, 0.14)",
        },
      },
    } satisfies ExtendComponent<TooltipFactory>,
    // Like the page's <details>: a muted line under a rule, which opens a list.
    Accordion: {
      styles: {
        item: { borderBottom: 0, borderTop: "1px solid var(--border)" },
        control: { padding: "10px 0 0", background: "transparent", color: "var(--muted)", fontSize: "12px" },
        label: { padding: 0 },
        content: { padding: "8px 0 0" },
      },
    } satisfies ExtendComponent<AccordionFactory>,
    Checkbox: { defaultProps: { radius: 4 }, styles: { input: box } } satisfies ExtendComponent<CheckboxFactory>,
    Popover: { styles: { dropdown: box } } satisfies ExtendComponent<PopoverFactory>,
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
