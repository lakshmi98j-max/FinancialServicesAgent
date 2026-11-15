"""The pre-push scanner must catch credentials and real email addresses, and pass fictional ones."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("scan_secrets", Path(__file__).parent.parent / "scripts" / "scan_secrets.py")
scan_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_secrets)


def test_flags_keys_and_real_emails():
    text = 'key = "sk-ant-' + "a" * 30 + '"\ncontact: someone@' + "gmail.com\n"
    labels = [h.split(": ", 1)[1] for h in scan_secrets.scan("f.py", text)]
    assert "Anthropic key" in labels and "Non-fictional email" in labels


def test_passes_reserved_domains():
    text = "priya.raman@example.com a@sub.example.org dev@mail.test x@corp.invalid"
    assert scan_secrets.scan("f.py", text) == []
