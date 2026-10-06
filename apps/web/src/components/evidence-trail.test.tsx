import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { EvidenceTrail } from "@/components/evidence-trail";
import type { FindingPublic } from "@/lib/api-client";

describe("EvidenceTrail", () => {
  it("shows 'No findings.' when there are none", () => {
    render(<EvidenceTrail findings={[]} />);
    expect(screen.getByText("No findings.")).toBeInTheDocument();
  });

  it("renders a finding's evidence trail, including evidence from files outside the diff", () => {
    const findings: FindingPublic[] = [
      {
        file_path: "app.py",
        line_start: 2,
        line_end: 2,
        category: "correctness",
        severity: "high",
        message: "breaks the caller",
        confidence: 0.98,
        agent_name: "cross_file",
        evidence: [
          {
            file_path: "alerts.py",
            line_start: 10,
            line_end: 14,
            reason: "resolved caller of the changed function",
          },
        ],
      },
    ];

    render(<EvidenceTrail findings={findings} />);

    expect(screen.getByText("breaks the caller")).toBeInTheDocument();
    expect(screen.getByText(/alerts\.py:10-14/)).toBeInTheDocument();
    expect(screen.getByText(/resolved caller of the changed function/)).toBeInTheDocument();
  });

  it("omits the evidence section for a finding with no evidence", () => {
    const findings: FindingPublic[] = [
      {
        file_path: "app.py",
        line_start: 1,
        line_end: 1,
        category: "correctness",
        severity: "medium",
        message: "speculative issue",
        confidence: 0.6,
        agent_name: "diff_only",
        evidence: [],
      },
    ];

    render(<EvidenceTrail findings={findings} />);

    expect(screen.queryByText(/evidence trail/i)).not.toBeInTheDocument();
  });
});
