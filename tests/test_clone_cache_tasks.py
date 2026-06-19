"""Tests for the /tasks additions to CloneCache: acquire_ref (checkout an
arbitrary branch), apply_patch, and collect_changes."""

import os
import subprocess
import tempfile
import unittest

from reviewbot.clone_cache import CloneCache


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


class AcquireRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = self._tmp.name
        self.src = os.path.join(root, "src")
        os.makedirs(self.src)
        _git(self.src, "init", "--quiet", "-b", "main")
        self._write("hello.txt", "hi from main\n")
        self._write("del.txt", "delete me\n")
        _git(self.src, "add", "-A")
        _git(self.src, "commit", "--quiet", "-m", "main commit")
        # A serge-owned fix branch.
        _git(self.src, "branch", "serge/fix-1")
        self.cache = CloneCache(os.path.join(root, "cache"))

    def _write(self, path, content):
        full = os.path.join(self.src, path)
        os.makedirs(os.path.dirname(full) or self.src, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    def _acquire(self, ref="main", job_id="job1"):
        return self.cache.acquire_ref(
            token="",
            owner="acme",
            repo="widget",
            ref=ref,
            job_id=job_id,
            remote_url=self.src,
        )

    def test_acquire_ref_checks_out_branch(self):
        co = self._acquire()
        self.assertIsNotNone(co)
        with open(os.path.join(co.path, "hello.txt")) as f:
            self.assertEqual(f.read(), "hi from main\n")

    def test_acquire_ref_serge_branch(self):
        co = self._acquire(ref="serge/fix-1", job_id="job2")
        self.assertIsNotNone(co)

    def test_acquire_ref_missing_branch_returns_none(self):
        co = self._acquire(ref="nope", job_id="jobX")
        self.assertIsNone(co)

    def test_apply_patch_and_collect_changes(self):
        co = self._acquire()
        patch = (
            "diff --git a/hello.txt b/hello.txt\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1 +1 @@\n"
            "-hi from main\n"
            "+hi patched\n"
            "diff --git a/del.txt b/del.txt\n"
            "deleted file mode 100644\n"
            "--- a/del.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-delete me\n"
            "diff --git a/new.txt b/new.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1 @@\n"
            "+brand new\n"
        )
        self.cache.apply_patch(co, patch)
        changes = {c.path: c for c in self.cache.collect_changes(co)}
        self.assertEqual(changes["hello.txt"].status, "M")
        self.assertEqual(changes["hello.txt"].content, b"hi patched\n")
        self.assertEqual(changes["del.txt"].status, "D")
        self.assertIsNone(changes["del.txt"].content)
        self.assertEqual(changes["new.txt"].status, "A")
        self.assertEqual(changes["new.txt"].content, b"brand new\n")

    def test_apply_patch_failure_raises(self):
        co = self._acquire()
        bad = (
            "diff --git a/hello.txt b/hello.txt\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1 +1 @@\n"
            "-this context does not match\n"
            "+whatever\n"
        )
        with self.assertRaises(subprocess.CalledProcessError):
            self.cache.apply_patch(co, bad)

    def test_apply_patch_recount_fallback_fixes_bad_line_numbers(self):
        # The @@ header line counts are wrong (claims 4 lines); strict
        # `git apply` rejects this as corrupt, mirroring the off-by-some
        # line numbers in LLM-authored diffs. The --recount fallback
        # recomputes them from the hunk body, so the edit still applies.
        co = self._acquire()
        bad_geometry = (
            "diff --git a/hello.txt b/hello.txt\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1,4 +1,4 @@\n"
            "-hi from main\n"
            "+hi patched\n"
        )
        # Strict apply alone fails on the bad geometry…
        strict = self.cache._git(
            co.path,
            "apply",
            "--index",
            "--whitespace=nowarn",
            self._patch_file(bad_geometry),
            check=False,
        )
        self.assertNotEqual(strict.returncode, 0)
        # …but apply_patch's recount fallback lands it.
        self.cache.apply_patch(co, bad_geometry)
        changes = {c.path: c for c in self.cache.collect_changes(co)}
        self.assertEqual(changes["hello.txt"].content, b"hi patched\n")

    def _patch_file(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False)
        self.addCleanup(os.unlink, fh.name)
        fh.write(text if text.endswith("\n") else text + "\n")
        fh.close()
        return fh.name


if __name__ == "__main__":
    unittest.main()
