"""Offline audit of every reachable Git blob and commit; never prints matches."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    'private_artifact': r'(?i)(?:^|/)(?:private|\.env)(?:/|$|\.)|\.(?:sqlite3?|db|dpapi|eml|msg|log)$',
    'email': r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',
    'windows_path': r'[A-Za-z]:[\\/][^\r\n\x00"\x27<>]+',
    'document_link': r'https?://docs\.qq\.com/[^\s"\x27<>]+',
    'phone': r'(?<!\d)1[3-9]\d{9}(?!\d)',
    'secret': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{24,})|eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}',
    'literal_credential': r'(?i)(?:mail_code|password|access_token|api_key|client_secret)\s*[\x22\x27]?\s*[:=]\s*[\x22\x27]([^\x22\x27\r\n]{8,})[\x22\x27]',
}

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def main():
    objects = git('rev-list', '--objects', '--all').decode('utf-8').splitlines()
    findings = []
    def inspect(oid, location, text, rules):
        for kind in rules:
            for match in re.finditer(RULES[kind], text):
                value = match.group()
                if kind == 'email' and value.endswith(('@example.invalid', '@example.com')):
                    continue
                findings.append({'kind': kind, 'object': oid, 'location': location,
                                 'line': text.count('\n', 0, match.start()) + 1,
                                 'fingerprint': hashlib.sha256(value.encode()).hexdigest()[:12]})
    count = 0
    for entry in objects:
        oid, _, path = entry.partition(' ')
        if git('cat-file', '-t', oid).strip() != b'blob':
            continue
        count += 1
        inspect(oid, path, path, ['private_artifact'])
        raw = git('cat-file', 'blob', oid)
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            try: text = raw.decode('utf-16')
            except UnicodeDecodeError:
                findings.append({'kind':'unreviewed_binary','object':oid,'location':path})
                continue
        inspect(oid, path, text, [k for k in RULES if k != 'private_artifact'])
    commits = git('rev-list', '--all').decode().splitlines()
    identities = set()
    for oid in commits:
        text = git('cat-file', 'commit', oid).decode('utf-8', errors='replace')
        inspect(oid, 'commit metadata/message', text, ['email','windows_path','phone','secret'])
        identities.update(re.findall(r'^(?:author|committer) (.+?) \d+ [+-]\d+$', text, re.M))
    report = {'scope':'all refs reachable commits and blobs; excludes ignored runtime and unreachable/reflog objects',
              'head':git('rev-parse','HEAD').decode().strip(), 'commits':len(commits), 'blobs':count,
              'identity_count':len(identities), 'findings':findings,
              'limitations':'Pattern scan requires manual review; not proof that all secrets or personal data are absent.'}
    output = ROOT/'private'/'public-history-audit.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('commits','blobs','identity_count')} | {
        'findings_by_kind':{k:sum(f['kind']==k for f in findings) for k in sorted({f['kind'] for f in findings})}}, ensure_ascii=False))

if __name__ == '__main__':
    main()
