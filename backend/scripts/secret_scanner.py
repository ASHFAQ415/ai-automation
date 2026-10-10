#!/usr/bin/env python3
"""Secret Scanner for AI Automation Platform."""
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|private[_-]?key)\s*[:=]\s*['\"][a-zA-Z0-9_-]{16,}['\"]"), "Generic Secret"),
    (re.compile(r"gsk_[a-zA-Z0-9]{30,}"), "Groq API Key"),
    (re.compile(r"8801434290:[a-zA-Z0-9_-]{30,}"), "Telegram Bot Token"),
    (re.compile(r"rzp_(live|test)_[a-zA-Z0-9]{14,}"), "Razorpay Key"),
    (re.compile(r"sk_(live|test)_[a-zA-Z0-9]{24,}"), "Stripe Secret Key"),
    (re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----"), "Private Key Block"),
]

ALLOWLIST_FILES = {
    ".env.example",
    "secret_scanner.py",
    "EXTERNAL_PREREQUISITES.md",
}

IGNORE_DIRS = {
    ".venv", "venv", ".git", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", "hermes"
}

def scan_file(file_path: Path):
    if file_path.name in ALLOWLIST_FILES:
        return []
    if any(part in IGNORE_DIRS for part in file_path.parts):
        return []
    
    findings = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    
    for line_num, line in enumerate(content.splitlines(), start=1):
        low = line.lower()
        if "your_" in low or "example" in low or "change-me" in low or "settings." in line or "process.env" in line:
            continue
        for pattern, desc in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append((line_num, desc, line.strip()))
    return findings

def main():
    root = Path(__file__).resolve().parent.parent.parent
    violations = 0
    scanned_count = 0
    
    for ext in ("*.py", "*.md", "*.html", "*.js", "*.json", "*.yaml", "*.yml"):
        for p in root.rglob(ext):
            if any(part in IGNORE_DIRS for part in p.parts):
                continue
            scanned_count += 1
            results = scan_file(p)
            if results:
                for line_num, desc, snippet in results:
                    masked = snippet[:20] + "...[MASKED]" if len(snippet) > 25 else "[MASKED]"
                    print(f"FAILED: {p.relative_to(root)}:{line_num} -> {desc}: {masked}")
                    violations += 1
    
    print(f"Scanned {scanned_count} files across core project repository.")
    if violations > 0:
        print(f"Security Alert: Found {violations} potential leaked secrets!")
        sys.exit(1)
    else:
        print("PASS: No leaked credentials or secrets found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
