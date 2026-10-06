import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  saveRunMetadata,
  getRunMetadata,
  useRunMetadata,
  type RunMetadata,
} from "@/lib/run-metadata-store";

const SAMPLE: RunMetadata = {
  repoFullName: "acme/widgets",
  baseBranch: "main",
  prNumber: 1,
  prTitle: "Fix off-by-one",
  prBody: "",
  diff: "--- a/app.py\n+++ b/app.py\n",
  agent: "diff_only",
};

describe("run-metadata-store", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("round-trips metadata through sessionStorage, keyed by run id", () => {
    saveRunMetadata("run-1", SAMPLE);
    expect(getRunMetadata("run-1")).toEqual(SAMPLE);
  });

  it("returns null for a run id that was never saved", () => {
    expect(getRunMetadata("never-saved")).toBeNull();
  });

  it("keeps different runs' metadata independent", () => {
    saveRunMetadata("run-a", SAMPLE);
    saveRunMetadata("run-b", { ...SAMPLE, prTitle: "Something else" });

    expect(getRunMetadata("run-a")?.prTitle).toBe("Fix off-by-one");
    expect(getRunMetadata("run-b")?.prTitle).toBe("Something else");
  });

  it("degrades to null instead of throwing when storage is unavailable", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });

    expect(() => getRunMetadata("run-1")).not.toThrow();
    expect(getRunMetadata("run-1")).toBeNull();

    spy.mockRestore();
  });

  it("does not throw when saving fails (e.g. quota exceeded)", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });

    expect(() => saveRunMetadata("run-1", SAMPLE)).not.toThrow();

    spy.mockRestore();
  });

  describe("useRunMetadata", () => {
    it("reads previously saved metadata for the given run id", () => {
      saveRunMetadata("run-hook", SAMPLE);

      const { result } = renderHook(() => useRunMetadata("run-hook"));

      expect(result.current).toEqual(SAMPLE);
    });

    it("returns null for a run with no saved metadata", () => {
      const { result } = renderHook(() => useRunMetadata("no-such-run"));

      expect(result.current).toBeNull();
    });
  });
});
