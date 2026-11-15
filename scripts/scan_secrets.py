"""Refuse to push if a staged file looks like it contains a credential or a real email address.

    python scripts/scan_secrets.py          # scans files staged in git
Exit code 1 means stop and look.

Real email addresses matter here because Claude Code attaches the signed-in account's email to every model call
(see agent/llm.py). Every legitimate address in this repo uses a reserved domain that can never belong to a person.
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
EMAIL = r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", ".test", ".invalid")   # RFC 2606


def is_reserved(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith(d if d.startswith(".") else "." + d) for d in RESERVED_EMAIL_DOMAINS)


def scan(path: str, text: str) -> list[str]:
    hits = []
    for label, pattern in PATTERNS.items():
        for m in re.finditer(pattern, text):
            hits.append(f"{path}:{text.count(chr(10), 0, m.start()) + 1}: {label}")
    for m in re.finditer(EMAIL, text):
        if not is_reserved(m.group(1)):
            hits.append(f"{path}:{text.count(chr(10), 0, m.start()) + 1}: Non-fictional email")
    return hits


def main() -> int:
    files = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                           capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for path in files:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except (IsADirectoryError, FileNotFoundError):
            continue
        hits += scan(path, text)
    if hits:
        print("Possible secrets in staged files:\n  " + "\n  ".join(hits))
        return 1
    print(f"No secrets or real email addresses found in {len(files)} staged file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
