import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { Providers } from "./Providers";

export const renderView = (view: ReactNode) => render(<Providers>{view}</Providers>);
