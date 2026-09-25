import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Vitest doesn't auto-run Testing Library's per-test DOM cleanup the way
// Jest's globals do; without this, each `render()` in a test file accumulates
// on top of the previous one and later assertions see stale elements.
afterEach(() => {
  cleanup();
});
