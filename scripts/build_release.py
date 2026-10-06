#!/usr/bin/env python3
"""Validate the public Markdown tree and package the portable skill."""
import argparse
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'clear-expression'
DOCS = ['usage.md', 'design.md', 'evaluation.md', 'sources.md', 'release-1.0.0.md']


def validate():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must use major.minor.patch')
    entry = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    if not entry.startswith('---\n') or '\nname: clear-expression\n' not in entry:
        raise ValueError('Missing skill frontmatter or incorrect name')
    if not re.search(r'^  version: "' + re.escape(version) + r'"$', entry, re.M):
        raise ValueError('Skill metadata version differs from VERSION')
    if list(SKILL.rglob('SKILL.md')) != [SKILL / 'SKILL.md']:
        raise ValueError('The package must have one discoverable SKILL.md')
    files = sorted(SKILL.rglob('*.md'))
    public = files + [ROOT / 'README.md', ROOT / 'CHANGELOG.md'] + [ROOT / 'docs' / n for n in DOCS]
    allowed = set(public) | {ROOT / 'LICENSE'}
    graph = []
    for path in public:
        if path.is_symlink():
            raise ValueError(f'Public file is a symlink: {path.relative_to(ROOT)}')
        text = path.read_text(encoding='utf-8')
        if re.search(r'/home/[^\s/]+/|\.codex/(?:sessions|attachments)/|[A-Z]:\\Users\\', text):
            raise ValueError(f'Private local path: {path.relative_to(ROOT)}')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            target = target.strip().split('#', 1)[0]
            if not target or re.match(r'[a-zA-Z][\w+.-]*:', target):
                continue
            dest = (path.parent / target).resolve()
            if dest not in allowed or not dest.is_file():
                raise ValueError(f'Invalid public link: {path.relative_to(ROOT)} -> {target}')
            if path in files:
                if dest not in files:
                    raise ValueError(f'Skill depends on external package file: {target}')
                graph.append((path, dest))
    reachable = {SKILL / 'SKILL.md'}
    while True:
        expanded = reachable | {dest for source, dest in graph if source in reachable}
        if expanded == reachable:
            break
        reachable = expanded
    if reachable != set(files):
        raise ValueError(f'Unreachable skill files: {set(files) - reachable}')
    return version, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate without writing a ZIP')
    args = parser.parse_args()
    version, files = validate()
    if args.check:
        print(f'Hypo-Expression {version}: public links and {len(files)} skill files valid.')
        return
    output = ROOT / 'dist' / f'clear-expression-{version}.zip'
    output.parent.mkdir(exist_ok=True)
    entries = [(p, 'clear-expression/' + p.relative_to(SKILL).as_posix()) for p in files]
    entries += [(ROOT / 'LICENSE', 'clear-expression/LICENSE')]
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, name in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 6, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP readback failed')
        for path, name in entries:
            if archive.read(name) != path.read_bytes():
                raise ValueError(f'ZIP content mismatch: {name}')
    print(output)


if __name__ == '__main__':
    main()
