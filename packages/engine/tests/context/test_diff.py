"""Unit tests for `revu.context.diff`, pinning exact parsed line ranges
against hand-constructed unified diffs."""

from revu.context.diff import LineKind, parse_diff

SINGLE_HUNK_MIXED = """\
diff --git a/foo.py b/foo.py
index 111..222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,5 @@
 line1
-line2
+line2 changed
+line2b new
 line3
 line4
"""

MULTI_HUNK_ONE_FILE = """\
diff --git a/bar.py b/bar.py
--- a/bar.py
+++ b/bar.py
@@ -1,2 +1,2 @@
-a
+A
 b
@@ -10,2 +10,3 @@
 x
+y
 z
"""

PURE_ADDITION = """\
diff --git a/new.py b/new.py
new file mode 100644
index 0000000..111111 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+line1
+line2
+line3
"""

PURE_DELETION = """\
diff --git a/old.py b/old.py
deleted file mode 100644
index 111111..0000000 100644
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-line1
-line2
-line3
"""

NO_TRAILING_NEWLINE = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1 +1 @@
-old line
\\ No newline at end of file
+new line
\\ No newline at end of file
"""


def test_single_hunk_mixed_context_added_removed_pins_line_numbers() -> None:
    file_diffs = parse_diff(SINGLE_HUNK_MIXED)
    assert len(file_diffs) == 1
    fd = file_diffs[0]
    assert fd.file_path == "foo.py"
    assert fd.old_path == "foo.py"
    assert fd.new_path == "foo.py"
    assert len(fd.hunks) == 1

    hunk = fd.hunks[0]
    assert (hunk.old_start, hunk.old_count) == (1, 4)
    assert (hunk.new_start, hunk.new_count) == (1, 5)
    assert len(hunk.lines) == 6

    l1, l2, l3, l4, l5, l6 = hunk.lines
    assert (l1.kind, l1.old_lineno, l1.new_lineno, l1.content) == (LineKind.CONTEXT, 1, 1, "line1")
    assert (l2.kind, l2.old_lineno, l2.new_lineno, l2.content) == (
        LineKind.REMOVED, 2, None, "line2",
    )
    assert (l3.kind, l3.old_lineno, l3.new_lineno) == (LineKind.ADDED, None, 2)
    assert l3.content == "line2 changed"
    assert (l4.kind, l4.old_lineno, l4.new_lineno) == (LineKind.ADDED, None, 3)
    assert l4.content == "line2b new"
    assert (l5.kind, l5.old_lineno, l5.new_lineno, l5.content) == (LineKind.CONTEXT, 3, 4, "line3")
    assert (l6.kind, l6.old_lineno, l6.new_lineno, l6.content) == (LineKind.CONTEXT, 4, 5, "line4")

    assert hunk.new_line_range() == (1, 5)
    assert hunk.changed_new_line_range() == (2, 3)
    assert hunk.removed_old_line_range() == (2, 2)


def test_multiple_hunks_in_one_file() -> None:
    file_diffs = parse_diff(MULTI_HUNK_ONE_FILE)
    assert len(file_diffs) == 1
    fd = file_diffs[0]
    assert fd.file_path == "bar.py"
    assert len(fd.hunks) == 2

    first, second = fd.hunks
    assert (first.old_start, first.old_count, first.new_start, first.new_count) == (1, 2, 1, 2)
    assert first.new_line_range() == (1, 2)
    assert first.changed_new_line_range() == (1, 1)
    assert first.removed_old_line_range() == (1, 1)

    assert (second.old_start, second.old_count, second.new_start, second.new_count) == (
        10, 2, 10, 3,
    )
    assert second.new_line_range() == (10, 12)
    assert second.changed_new_line_range() == (11, 11)
    assert second.removed_old_line_range() is None


def test_multiple_files_in_one_diff() -> None:
    combined = SINGLE_HUNK_MIXED + MULTI_HUNK_ONE_FILE
    file_diffs = parse_diff(combined)
    assert len(file_diffs) == 2
    assert file_diffs[0].file_path == "foo.py"
    assert file_diffs[1].file_path == "bar.py"
    assert len(file_diffs[0].hunks) == 1
    assert len(file_diffs[1].hunks) == 2


def test_pure_addition_hunk() -> None:
    file_diffs = parse_diff(PURE_ADDITION)
    assert len(file_diffs) == 1
    fd = file_diffs[0]
    assert fd.is_addition is True
    assert fd.old_path is None
    assert fd.new_path == "new.py"
    assert fd.file_path == "new.py"

    hunk = fd.hunks[0]
    assert (hunk.old_start, hunk.old_count) == (0, 0)
    assert (hunk.new_start, hunk.new_count) == (1, 3)
    assert all(line.kind is LineKind.ADDED for line in hunk.lines)
    assert [line.new_lineno for line in hunk.lines] == [1, 2, 3]
    assert hunk.new_line_range() == (1, 3)
    assert hunk.changed_new_line_range() == (1, 3)
    assert hunk.removed_old_line_range() is None


def test_pure_deletion_hunk() -> None:
    file_diffs = parse_diff(PURE_DELETION)
    assert len(file_diffs) == 1
    fd = file_diffs[0]
    assert fd.is_deletion is True
    assert fd.old_path == "old.py"
    assert fd.new_path is None
    assert fd.file_path == "old.py"  # falls back to old_path once new_path is gone

    hunk = fd.hunks[0]
    assert (hunk.old_start, hunk.old_count) == (1, 3)
    assert (hunk.new_start, hunk.new_count) == (0, 0)
    assert all(line.kind is LineKind.REMOVED for line in hunk.lines)
    assert [line.old_lineno for line in hunk.lines] == [1, 2, 3]
    assert hunk.removed_old_line_range() == (1, 3)
    assert hunk.changed_new_line_range() is None
    # No new-file lines survive; falls back to a single point at new_start.
    assert hunk.new_line_range() == (1, 1)


def test_diff_with_no_trailing_newline_marker_is_not_counted_as_content() -> None:
    file_diffs = parse_diff(NO_TRAILING_NEWLINE)
    assert len(file_diffs) == 1
    hunk = file_diffs[0].hunks[0]
    assert len(hunk.lines) == 2  # the two "\ No newline..." marker lines are excluded
    assert hunk.lines[0].kind is LineKind.REMOVED
    assert hunk.lines[0].content == "old line"
    assert hunk.lines[1].kind is LineKind.ADDED
    assert hunk.lines[1].content == "new line"


def test_diff_with_no_extended_git_header_still_parses() -> None:
    """A plain unified diff (just ---/+++/@@, no `diff --git` block) parses
    the same way - some tools emit diffs without the extended header."""
    plain = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-old\n+new\n context\n"
    file_diffs = parse_diff(plain)
    assert len(file_diffs) == 1
    assert file_diffs[0].file_path == "x.py"
    assert len(file_diffs[0].hunks[0].lines) == 3
