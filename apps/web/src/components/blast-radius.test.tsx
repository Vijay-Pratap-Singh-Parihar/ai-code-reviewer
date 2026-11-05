import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BlastRadiusView } from "@/components/blast-radius";
import type { BlastRadius } from "@/lib/api-client";

const RADIUS: BlastRadius = {
  depth: 2,
  changed: 1,
  impacted: 2,
  impacted_files: 2,
  truncated: false,
  nodes: [
    { id: 1, name: "pkg.core.normalize", kind: "function", file_path: "pkg/core.py", line_start: 1, line_end: 2, distance: 0 },
    { id: 2, name: "pkg.service.load", kind: "function", file_path: "pkg/service.py", line_start: 4, line_end: 5, distance: 1 },
    { id: 3, name: "pkg.api.handler", kind: "function", file_path: "pkg/api.py", line_start: 4, line_end: 5, distance: 2 },
  ],
  edges: [
    { caller: 2, callee: 1 },
    { caller: 3, callee: 2 },
  ],
};

describe("BlastRadiusView", () => {
  it("summarises the radius and lays symbols out by call distance", () => {
    render(<BlastRadiusView radius={RADIUS} />);

    expect(screen.getByText(/called by/)).toHaveTextContent(
      "1 changed symbol is called by 2 other symbols in 2 other files, up to 2 calls away.",
    );
    expect(screen.getByText("Changed")).toBeInTheDocument();
    expect(screen.getByText("Direct callers")).toBeInTheDocument();
    expect(screen.getByText("2 calls away")).toBeInTheDocument();
    expect(screen.getByText("core.normalize")).toBeInTheDocument();
  });

  it("names a symbol and its location when it is pointed at", () => {
    render(<BlastRadiusView radius={RADIUS} />);

    fireEvent.mouseEnter(screen.getByRole("button", { name: /pkg\.service\.load/ }));

    expect(screen.getByText("pkg.service.load · pkg/service.py:4-5 · called by 1 shown · calls 1 shown")).toBeInTheDocument();
  });

  it("lists the affected code by file", () => {
    render(<BlastRadiusView radius={RADIUS} />);
    expect(screen.getByText("pkg/api.py")).toBeInTheDocument();
    expect(screen.getByText(/calls a changed symbol/)).toBeInTheDocument();
  });

  it("explains an empty radius", () => {
    render(<BlastRadiusView radius={{ ...RADIUS, changed: 0, impacted: 0, nodes: [], edges: [] }} />);
    expect(screen.getByText(/nothing to trace/)).toBeInTheDocument();
  });
});
