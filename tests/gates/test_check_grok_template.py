"""``check_grok_template.checkout_state``: the release-pin check ``routine.py`` and ``desk_doctor.py`` call (PR 2d
decision D2-7 with the integrator's override O10).

The pin check runs four literal git argument lists and nothing else; every reason it gives is pinned here through
the ``run`` seam (a fake that answers like git), and the same reasons are then watched on a real throwaway
repository: pinned, dirty, a moved HEAD, a development checkout without the tag, and a directory that is no
checkout. git's refusal of a checkout another user owns ("detected dubious ownership") is its own reason, so the
owner is sent to the SETUP step instead of being told the checkout is missing.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

# isort: off
import tests._path  # noqa: F401
import check_grok_template
# isort: on

HEAD = "971186c676d34b0a5d4e35c691d7888632132b8a"
OTHER = "0123456789abcdef0123456789abcdef01234567"
INSIDE = ["rev-parse", "--is-inside-work-tree"]
HEAD_ARGS = ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]
TAG_ARGS = ["rev-parse", "--verify", "--quiet", "refs/tags/v0.1.0^{commit}"]
STATUS_ARGS = ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]
DUBIOUS = (b"fatal: detected dubious ownership in repository at '/workspace/aegis-grok-desk'\n"
           b"To add an exception for this directory, call:\n\n"
           b"\tgit config --global --add safe.directory /workspace/aegis-grok-desk\n")
KEYS = {"git", "work_tree", "head", "tag", "tag_commit", "dirty", "pinned", "reason"}


class Answer:
    """What ``subprocess.run(..., capture_output=True)`` returns: bytes on both streams."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGit:
    """The ``run`` seam: records every call and answers by argument list."""

    def __init__(self, answers: Dict[Tuple[str, ...], Optional[Answer]]) -> None:
        self.answers = answers
        self.calls: List[Tuple[str, List[str]]] = []

    def __call__(self, root: str, args: List[str]) -> Optional[Answer]:
        self.calls.append((root, list(args)))
        return self.answers.get(tuple(args), Answer(128, b"", b"fatal: unexpected arguments\n"))


def repository(tag: Optional[str] = HEAD, status: bytes = b"", head: Optional[str] = HEAD) -> FakeGit:
    return FakeGit({
        tuple(INSIDE): Answer(0, b"true\n"),
        tuple(HEAD_ARGS): Answer(0, (head + "\n").encode()) if head else Answer(1),
        tuple(TAG_ARGS): Answer(0, (tag + "\n").encode()) if tag else Answer(1),
        tuple(STATUS_ARGS): Answer(0, status),
    })


class CheckoutStateSeamTest(unittest.TestCase):
    def state(self, fake: FakeGit, release: str = "0.1.0") -> Dict[str, Any]:
        result = check_grok_template.checkout_state("/r", release, run=fake)
        self.assertEqual(KEYS, set(result))
        return result

    def test_pinned_release_at_the_tag_commit_with_a_clean_tree(self) -> None:
        fake = repository()
        result = self.state(fake)
        self.assertEqual({"git": True, "work_tree": True, "head": HEAD, "tag": "v0.1.0", "tag_commit": HEAD,
                          "dirty": 0, "pinned": True, "reason": "release v0.1.0 at 971186c6, clean"}, result)
        self.assertEqual([("/r", INSIDE), ("/r", HEAD_ARGS), ("/r", TAG_ARGS), ("/r", STATUS_ARGS)], fake.calls)

    def test_only_the_four_literal_argument_lists_are_used(self) -> None:
        for fake in (repository(), repository(tag=None), repository(tag=OTHER), repository(status=b" M x\n")):
            self.state(fake)
            for _root, args in fake.calls:
                self.assertIn(args, (INSIDE, HEAD_ARGS, TAG_ARGS, STATUS_ARGS))
                self.assertNotIn("-c", args)

    def test_development_checkout_without_the_tag(self) -> None:
        result = self.state(repository(tag=None, status=b"?? notes.txt\n"))
        self.assertEqual((False, "development checkout (no tag)", None, HEAD, 1),
                         (result["pinned"], result["reason"], result["tag_commit"], result["head"], result["dirty"]))

    def test_head_off_the_tag_commit(self) -> None:
        result = self.state(repository(tag=OTHER))
        self.assertEqual((False, "HEAD 971186c6 is not the commit of v0.1.0 (01234567)"), (result["pinned"], result["reason"]))

    def test_uncommitted_and_untracked_paths(self) -> None:
        result = self.state(repository(status=b" M scripts/routine.py\n?? state/heartbeat/book-check.json\n"))
        self.assertEqual((False, 2, "the checkout has 2 uncommitted or untracked path(s)"),
                         (result["pinned"], result["dirty"], result["reason"]))

    def test_git_unavailable(self) -> None:
        result = self.state(FakeGit({tuple(INSIDE): None}))
        self.assertEqual((False, False, False, "git is unavailable"),
                         (result["git"], result["work_tree"], result["pinned"], result["reason"]))

    def test_not_a_git_checkout(self) -> None:
        fake = FakeGit({tuple(INSIDE): Answer(128, b"", b"fatal: not a git repository (or any of the parent directories): .git\n")})
        result = self.state(fake)
        self.assertEqual((True, False, False, "not a git checkout"),
                         (result["git"], result["work_tree"], result["pinned"], result["reason"]))
        self.assertEqual([("/r", INSIDE)], fake.calls, "nothing else is asked of a directory that is no checkout")

    def test_dubious_ownership_is_its_own_reason(self) -> None:
        result = self.state(FakeGit({tuple(INSIDE): Answer(128, b"", DUBIOUS)}))
        self.assertEqual((True, False, False, "git refuses this checkout: it is owned by another user (see SETUP)"),
                         (result["git"], result["work_tree"], result["pinned"], result["reason"]))
        self.assertEqual(check_grok_template.DUBIOUS_OWNERSHIP_REASON, result["reason"])
        # A text-mode seam (str streams) is read the same way.
        result = self.state(FakeGit({tuple(INSIDE): Answer(128, "", DUBIOUS.decode())}))  # type: ignore[arg-type]
        self.assertEqual(check_grok_template.DUBIOUS_OWNERSHIP_REASON, result["reason"])

    def test_release_must_be_a_plain_version(self) -> None:
        for release in ("v0.1.0", "0.1", "0.1.0-rc1", "", " 0.1.0", None, 1):
            with self.subTest(release=release):
                with self.assertRaises(ValueError):
                    check_grok_template.checkout_state("/r", release, run=repository())  # type: ignore[arg-type]

    def test_run_defaults_to_run_git(self) -> None:
        fake = repository()
        with mock.patch.object(check_grok_template, "run_git", fake):
            result = check_grok_template.checkout_state("/r", "0.1.0")
        self.assertTrue(result["pinned"])
        self.assertEqual(4, len(fake.calls))


class CheckoutStateRealGitTest(unittest.TestCase):
    """The same reasons from git itself, on a throwaway repository under a private HOME."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-checkout-state-")
        base = os.path.realpath(self._tmp.name)
        self.home = os.path.join(base, "home")
        self.repo = os.path.join(base, "repo")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        # A private HOME and no system config: the owner's git configuration cannot change what the check sees, and
        # the ceiling keeps git from finding a repository above the temporary directory.
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "XDG_CONFIG_HOME": self.home,
                                                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": base})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git("init", "-q")
        self.write("README.md", "desk\n")
        self.git("add", "README.md")
        self.commit("first")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True, check=False)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return proc.stdout.strip()

    def commit(self, message: str) -> None:
        self.git("-c", "user.name=Aegis Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", message)

    def write(self, rel: str, text: str) -> None:
        with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_tag_dirt_and_a_moved_head(self) -> None:
        state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertEqual((True, True, False, "development checkout (no tag)", 0),
                         (state["git"], state["work_tree"], state["pinned"], state["reason"], state["dirty"]))
        self.git("tag", "v0.1.0")
        head = self.git("rev-parse", "HEAD")
        state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertEqual((True, head, head, "release v0.1.0 at %s, clean" % head[:8]),
                         (state["pinned"], state["head"], state["tag_commit"], state["reason"]))
        self.write("README.md", "desk, edited\n")
        self.write("notes.txt", "untracked\n")
        state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertEqual((False, 2, "the checkout has 2 uncommitted or untracked path(s)"),
                         (state["pinned"], state["dirty"], state["reason"]))
        self.git("add", "README.md", "notes.txt")
        self.commit("second")
        moved = self.git("rev-parse", "HEAD")
        state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertEqual((False, "HEAD %s is not the commit of v0.1.0 (%s)" % (moved[:8], head[:8])),
                         (state["pinned"], state["reason"]))
        self.assertEqual("", self.git("--no-optional-locks", "status", "--porcelain"), "the check changed nothing")

    def hook(self) -> Tuple[str, str]:
        """An fsmonitor hook that leaves a marker when git runs it, and the marker's path."""
        marker = os.path.join(os.path.dirname(self.repo), "hook-ran.txt")
        hook = os.path.join(os.path.dirname(self.repo), "hook.sh")
        with open(hook, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho ran >> '%s'\nexit 0\n" % marker)
        os.chmod(hook, 0o755)
        return hook, marker

    def test_the_caller_s_git_environment_is_never_git_s(self) -> None:
        """A GIT_CONFIG_* override set in-process (core.fsmonitor runs a command during git status) and a GIT_DIR that
        re-points git are dropped: the pin check runs git with its own environment."""
        hook, marker = self.hook()
        self.git("tag", "v0.1.0")
        clean = "release v0.1.0 at %s, clean" % self.git("rev-parse", "HEAD")[:8]
        override = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": hook}
        with mock.patch.dict(os.environ, override):
            state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertFalse(os.path.exists(marker), "git ran the caller's fsmonitor hook")
        self.assertEqual((True, clean), (state["pinned"], state["reason"]))
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(os.path.dirname(self.repo), "elsewhere")}):
            state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertEqual((True, clean), (state["pinned"], state["reason"]), "a GIT_DIR of the caller re-points nothing")

    def test_the_repository_s_own_fsmonitor_and_hooks_never_run(self) -> None:
        hook, marker = self.hook()
        self.git("config", "core.fsmonitor", hook)
        self.write("README.md", "desk, edited\n")
        state = check_grok_template.checkout_state(self.repo, "0.1.0")
        self.assertFalse(os.path.exists(marker), "git ran the repository's fsmonitor hook")
        self.assertEqual(1, state["dirty"])

    def test_the_environment_git_gets(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_EXEC_PATH": "/x", "GIT_SSH_COMMAND": "x",
                                          "GIT_CONFIG_PARAMETERS": "'core.fsmonitor'='x'", "HOME": self.home}):
            env = check_grok_template.git_environment()
        kept = ("GIT_CEILING_DIRECTORIES", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
                "GIT_CONFIG_COUNT")
        stray = [key for key in env if key.startswith("GIT_") and key not in kept
                 and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]
        self.assertEqual([], sorted(stray))
        forced = {env["GIT_CONFIG_KEY_%d" % index]: env["GIT_CONFIG_VALUE_%d" % index]
                  for index in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual({"core.fsmonitor": "false", "core.hooksPath": os.devnull, "core.attributesFile": os.devnull},
                         forced)
        self.assertEqual(self.home, env["HOME"], "the owner's own git configuration (safe.directory) still applies")

    def test_a_plain_directory_is_not_a_checkout(self) -> None:
        plain = os.path.join(os.path.dirname(self.repo), "plain")
        os.makedirs(plain)
        state = check_grok_template.checkout_state(plain, "0.1.0")
        self.assertEqual((True, False, False, "not a git checkout"),
                         (state["git"], state["work_tree"], state["pinned"], state["reason"]))


if __name__ == "__main__":
    unittest.main()
