const OLD_HEADER = /^--- (?:a\/(.+)|\/dev\/null)\s*$/;
const NEW_HEADER = /^\+\+\+ (?:b\/(.+)|\/dev\/null)\s*$/;

/**
 * `gitdiff-parser` (which `react-diff-view`'s `parseDiff` is built on) only
 * ever reads a file's path off a `diff --git a/X b/Y` header line — it
 * parses a file's `---`/`+++`/hunk lines just fine without one, but
 * `oldPath`/`newPath` both come back as empty strings, which silently
 * breaks anything that matches a `Finding.file_path` against them (found by
 * actually rendering a real run's diff and finding the inline widget never
 * appeared, not by reading the library's source).
 *
 * Many hand-typed or copy-pasted unified diffs — including this app's own
 * example diff in the trigger form — are exactly this: `---`/`+++`/`@@`
 * lines with no `diff --git` line. Rather than require callers to always
 * paste a full `git diff` output, synthesize the missing header per file
 * block from its own `---`/`+++` lines. A diff that already has at least
 * one `diff --git` line is assumed to be complete and left untouched.
 */
export function ensureGitDiffHeaders(diffText: string): string {
  if (/^diff --git /m.test(diffText)) {
    return diffText;
  }

  const lines = diffText.split("\n");
  const result: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const oldMatch = OLD_HEADER.exec(lines[i]);
    const newMatch = oldMatch ? NEW_HEADER.exec(lines[i + 1] ?? "") : null;

    if (oldMatch && newMatch) {
      const oldPath = oldMatch[1];
      const newPath = newMatch[1];
      result.push(`diff --git a/${oldPath ?? newPath} b/${newPath ?? oldPath}`);
    }

    result.push(lines[i]);
  }

  return result.join("\n");
}
