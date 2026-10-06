import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DiffViewer } from "@/components/diff-viewer";
import type { FindingPublic } from "@/lib/api-client";

const TWO_FILE_DIFF = `diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
-old a
+new a
 unchanged a
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -1,2 +1,2 @@
-old b
+new b
 unchanged b
`;

function finding(overrides: Partial<FindingPublic>): FindingPublic {
  return {
    file_path: "a.py",
    line_start: 1,
    line_end: 1,
    category: "correctness",
    severity: "high",
    message: "something is off",
    confidence: 0.9,
    agent_name: "diff_only",
    evidence: [],
    ...overrides,
  };
}

const NO_HEADER_DIFF = `--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
-for i in range(n):
+for i in range(n + 1):
     total += values[i]
`;

describe("DiffViewer", () => {
  it("anchors a finding even on a diff with no 'diff --git' header line (e.g. the trigger form's own example diff)", () => {
    // Regression test: found by actually rendering a real run's diff and
    // seeing the inline widget never appear — gitdiff-parser silently
    // returns an empty file path without this header, which broke the
    // file-path match in DiffViewer for exactly the diff shape the
    // trigger form itself pre-fills (see lib/diff-utils.ts).
    render(
      <DiffViewer
        diffText={NO_HEADER_DIFF}
        findings={[finding({ file_path: "app.py", line_start: 2, line_end: 2, message: "off-by-one" })]}
      />,
    );

    expect(screen.getByText("app.py")).toBeInTheDocument();
    expect(screen.getByText("off-by-one")).toBeInTheDocument();
  });

  it("renders every file in a multi-file diff", () => {
    render(<DiffViewer diffText={TWO_FILE_DIFF} findings={[]} />);

    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("b.py")).toBeInTheDocument();
  });

  it("anchors a finding only under the file it actually targets", () => {
    render(
      <DiffViewer
        diffText={TWO_FILE_DIFF}
        findings={[finding({ file_path: "b.py", line_start: 1, line_end: 1, message: "b-only issue" })]}
      />,
    );

    expect(screen.getByText("b-only issue")).toBeInTheDocument();
  });

  it("does not render a finding whose line range doesn't match any changed line", () => {
    render(
      <DiffViewer
        diffText={TWO_FILE_DIFF}
        findings={[finding({ file_path: "a.py", line_start: 999, line_end: 999, message: "out of range" })]}
      />,
    );

    expect(screen.queryByText("out of range")).not.toBeInTheDocument();
  });

  it("shows a graceful message instead of crashing on an unparseable diff", () => {
    render(<DiffViewer diffText="not a real diff" findings={[]} />);

    expect(screen.getByText(/couldn't parse this diff/i)).toBeInTheDocument();
  });
});
