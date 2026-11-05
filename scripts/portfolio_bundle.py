"""Build an explicit public snapshot without modifying local history or secrets.

No Git commands or network writes. The destination must be a new directory.
The private manifest is placed beside the export, outside its publishable files.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def files():
    selected = set()
    roots = ['backend', 'frontend', 'runtime', 'firestore', 'docs/copilot',
             'portfolio_notes/assets', 'portfolio_notes/evidence',
             '발주량 예측 모델/src', '발주량 예측 모델/docs',
             '발주량 예측 모델/models/audited_v2', '발주량 예측 모델/models/audited_v3', '발주량 예측 모델/outputs',
             '발주량 예측 모델/Dataset_공급망 최적화 AI 데이터셋/data',
             '배터리 예지보전 모델/src', '배터리 예지보전 모델/docs',
             '배터리 예지보전 모델/outputs/track_b_final_v2',
             '배터리 품질보증 모델/src', '배터리 품질보증 모델/output/models']
    for directory in roots:
        for path in (ROOT / directory).rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(ROOT)
            if any(p in relative.parts for p in ('__pycache__', '.git', 'audited_v2_initial')):
                continue
            if path.suffix in ('.pyc', '.log') or 'firebase-adminsdk' in path.name:
                continue
            if directory.endswith('track_b_final_v2') and path.suffix == '.joblib':
                continue
            selected.add(path)
    names = ['README.md', 'GETTING_STARTED.md', 'GITHUB_DESCRIPTION.txt',
             'PORTFOLIO_RUN.md', 'FIRESTORE_MIGRATION_20261007.md',
             'QUALITY_THRESHOLD_REPORT.md', 'QUALITY_WORKSPACE_PLAN.md',
             'DESIGN.md', '.gitignore', '.gitattributes', '.env.example',
             'requirements-local.txt', 'run_local.py', 'setup_local.cmd',
             'start_portfolio.cmd', 'start_demo.cmd', 'start_local.cmd', 'start_local.ps1',
             'portfolio_notes/01_포트폴리오_핵심내용.md',
             'portfolio_notes/maintenance/firestore_migration_20261007.json',
             '발주량 예측 모델/requirements.txt', '발주량 예측 모델/README.md',
             '발주량 예측 모델/DATA_SOURCES.md',
             '발주량 예측 모델/tests/test_audited_v2.py',
             '배터리 예지보전 모델/MODEL_CARD.md', '배터리 예지보전 모델/DATA_DICTIONARY.md',
             'scripts/portfolio_bundle.py', 'scripts/migrate_personal_firestore.py',
             'scripts/verify_connected_workflow.py', 'scripts/evaluate_copilot.py',
             'scripts/package_runtime.py', 'scripts/package_maintenance.py',
             'scripts/package_quality.py', 'scripts/package_demand_evidence.py',
             'tools/export_battery_pack_demo.py', 'tools/calibrate_pack_defects.py']
    for name in names:
        selected.add(ROOT / name)
    # Include exact source evidence referenced by the current seed's hashes.
    # Keeping the seed without these files would break its provenance checks.
    manifest = json.loads((ROOT / 'firestore/seed/manifest.json').read_text(encoding='utf-8'))
    for artifact in manifest['sourceArtifacts']:
        selected.add(ROOT / artifact['path'])
    for path in selected:
        if not path.is_file():
            raise ValueError('Required file missing: ' + str(path.relative_to(ROOT)))
        if path.stat().st_size >= 100 * 1024**2:
            raise ValueError('Oversize Git file: ' + str(path.relative_to(ROOT)))
    return sorted(selected)


def public_bytes(path):
    raw = path.read_bytes()
    if path.name == '01_포트폴리오_핵심내용.md':
        text = raw.decode('utf-8-sig')
        text = text.replace('C:/Users/USER/Desktop/Predictive_Maintenance/portfolio_notes/', '')
        text = text.replace('C:/Users/USER/Desktop/Predictive_Maintenance/', '../')
        text = '\n'.join(line for line in text.splitlines() if '02_개발과정_면접_학습노트.md' not in line) + '\n'
        raw = text.encode('utf-8')
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy-to', type=Path, required=True)
    args = parser.parse_args()
    destination = args.copy_to.resolve()
    if destination.exists():
        raise SystemExit('Destination already exists. Use a new empty path.')
    from dotenv import dotenv_values
    secrets = [str(v).encode() for k, v in dotenv_values(ROOT / '.env', interpolate=False).items()
               if v and len(str(v)) >= 12 and ('KEY' in k or 'PASSWORD' in k or 'SECRET' in k)]
    signatures = [rb'AIza[0-9A-Za-z_-]{35}', rb'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----',
                  rb'gh[pousr]_[A-Za-z0-9]{20,}', rb'github_pat_[A-Za-z0-9_]{30,}']
    payload = [(p, public_bytes(p)) for p in files()]
    bad = []
    for path, raw in payload:
        if any(s in raw for s in secrets) or any(re.search(pattern, raw) for pattern in signatures):
            bad.append(path.relative_to(ROOT).as_posix())
    if bad:
        raise SystemExit('Secret scan requires review (values suppressed): ' + ', '.join(bad))
    manifest = []
    for path, raw in payload:
        relative = path.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest.append({'path': relative.as_posix(), 'bytes': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest(),
                         'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    report = {'files': len(manifest), 'bytes': sum(p['bytes'] for p in manifest),
              'secretScan': 'passed: current .env keys and credential signatures', 'entries': manifest}
    destination.with_suffix('.manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'entries'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
