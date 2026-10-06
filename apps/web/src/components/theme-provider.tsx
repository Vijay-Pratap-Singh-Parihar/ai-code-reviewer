"use client";

import type { ReactNode } from "react";
import { ThemeProvider as NextThemeProvider } from "next-themes";

/**
 * `attribute="class"` matches globals.css's `@custom-variant dark
 * (&:is(.dark *))` — the app's dark-mode tokens are already keyed off a
 * `.dark` ancestor class, this just drives that class instead of requiring
 * a second, parallel dark-mode mechanism. `enableSystem` defaults new
 * visitors to their OS preference rather than hardcoding light.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemeProvider attribute="class" defaultTheme="system" enableSystem>
      {children}
    </NextThemeProvider>
  );
}
