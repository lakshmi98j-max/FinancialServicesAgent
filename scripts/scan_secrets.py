"""Refuse to push if a staged file looks like it contains a credential.

    python scripts/scan_secrets.py          # scans files staged in git
Exit code 1 means stop and look.
"""
import re
import subprocess
import sys

PATTERNS = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_\-]{10,}",
    "OpenAI-style key": r"sk-[A-Za-z0-9]{32,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "Private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "Assigned secret": r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]",
}


def main() -> int:
    files = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                           capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for path in files:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except (IsADirectoryError, FileNotFoundError):
            continue
        for label, pattern in PATTERNS.items():
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path}:{line}: {label}")
    if hits:
        print("Possible secrets in staged files:\n  " + "\n  ".join(hits))
        return 1
    print(f"No secrets found in {len(files)} staged file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
