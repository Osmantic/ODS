#!/usr/bin/env python3
"""Reject placeholder or machine-local git identities on the commits a PR adds.

  check-commit-identities.py BASE HEAD

Every author and committer must have a name that is not a template placeholder
("User Name") and an email on a real domain: not a reserved example domain
(RFC 2606, RFC 6761), and not a machine-local name such as `portal@local`,
`root@DESKTOP-ABC1234` or `me@laptop.local`. GitHub noreply addresses are fine.
"""
import subprocess
import sys

PLACEHOLDER_NAMES = {'user name', 'your name', 'name', 'user', 'root', 'unknown', 'username'}
# Final labels that never belong to a deliverable mail domain.
LOCAL_SUFFIXES = {'example', 'invalid', 'localhost', 'test', 'local', 'localdomain', 'lan', 'internal', 'home'}
EXAMPLE_DOMAINS = {'example.com', 'example.net', 'example.org'}


def problem(name, email):
    """Return why an identity is not acceptable, or None."""
    if name.strip().lower() in PLACEHOLDER_NAMES:
        return f'placeholder name "{name}"'
    _, at, domain = email.strip().rpartition('@')
    domain = domain.lower().rstrip('.')
    if not at or '.' not in domain:
        return f'email "{email}" has no real domain'
    labels = domain.split('.')
    if labels[-1] in LOCAL_SUFFIXES or domain.endswith('.home.arpa'):
        return f'email "{email}" uses a machine-local or reserved domain'
    if '.'.join(labels[-2:]) in EXAMPLE_DOMAINS:
        return f'email "{email}" uses a reserved example domain'
    return None


def commits(base, head):
    output = subprocess.run(
        ['git', 'log', '--format=%H%x00%an%x00%ae%x00%cn%x00%ce', f'{base}..{head}'],
        check=True, capture_output=True, text=True).stdout
    for line in output.splitlines():
        sha, author, author_email, committer, committer_email = line.split('\0')
        yield sha, (('author', author, author_email), ('committer', committer, committer_email))


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    base, head = sys.argv[1:]
    failures = checked = 0
    for sha, roles in commits(base, head):
        checked += 1
        for role, name, email in roles:
            reason = problem(name, email)
            if reason:
                failures += 1
                print(f'::error::{sha[:12]} {role} {name} <{email}>: {reason}')
    if failures:
        print(f'{failures} placeholder or machine-local identities in {checked} commits.\n'
              'Set a real identity (your GitHub noreply address works):\n'
              '  git config user.name "Your Real Name"\n'
              '  git config user.email "ID+LOGIN@users.noreply.github.com"\n'
              f'then rewrite this branch\'s commits and force-push:\n'
              f'  git rebase -r {base[:12]} --exec "git commit --amend --no-edit --reset-author"')
        return 1
    print(f'[PASS] {checked} commits have real author and committer identities')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
