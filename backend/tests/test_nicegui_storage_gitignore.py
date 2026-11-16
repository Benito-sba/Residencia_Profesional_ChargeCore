import subprocess
import unittest
from pathlib import Path


class NiceGUIStorageGitIgnoreTests(unittest.TestCase):
    def test_local_storage_is_ignored_and_not_tracked(self):
        repository_root = Path(__file__).resolve().parents[2]
        storage_paths = (
            ".nicegui/storage-user.json",
            "frontend/.nicegui/storage-user.json",
        )

        for storage_path in storage_paths:
            with self.subTest(path=storage_path):
                result = subprocess.run(
                    [
                        "git",
                        "check-ignore",
                        "--quiet",
                        "--no-index",
                        storage_path,
                    ],
                    cwd=repository_root,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)

        tracked = subprocess.run(
            ["git", "ls-files", "--", ".nicegui", "frontend/.nicegui"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(tracked.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
