import { describe, it, expect } from "vitest";
import { ensureGitDiffHeaders } from "@/lib/diff-utils";

describe("ensureGitDiffHeaders", () => {
  it("synthesizes a diff --git header for a plain ---/+++ diff with none", () => {
    const diff = `--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
-for i in range(n):
+for i in range(n + 1):
     total += values[i]
`;

    const result = ensureGitDiffHeaders(diff);

    expect(result).toContain("diff --git a/app.py b/app.py");
    // Nothing else about the diff's content should change.
    expect(result).toContain("--- a/app.py");
    expect(result).toContain("+++ b/app.py");
    expect(result).toContain("-for i in range(n):");
  });

  it("leaves a diff that already has a diff --git header untouched", () => {
    const diff = `diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
`;

    expect(ensureGitDiffHeaders(diff)).toBe(diff);
  });

  it("synthesizes a header per file in a multi-file diff with none", () => {
    const diff = `--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old a
+new a
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-old b
+new b
`;

    const result = ensureGitDiffHeaders(diff);

    expect(result).toContain("diff --git a/a.py b/a.py");
    expect(result).toContain("diff --git a/b.py b/b.py");
  });

  it("handles a new file (/dev/null old side)", () => {
    const diff = `--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+hello
`;

    expect(ensureGitDiffHeaders(diff)).toContain("diff --git a/new.py b/new.py");
  });

  it("handles a deleted file (/dev/null new side)", () => {
    const diff = `--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-hello
`;

    expect(ensureGitDiffHeaders(diff)).toContain("diff --git a/gone.py b/gone.py");
  });
});
