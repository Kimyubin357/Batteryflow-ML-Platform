"""Copy analysis-only Firestore data into a different personal project.

The source is read-only. Local official seed is authoritative. Existing target
documents must be identical; differences abort the entire preflight, never
overwrite/delete. Typed JSONL is a restartable backup, not a training input.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.cloud import firestore
from google.cloud.firestore_v1 import _helpers
from google.cloud.firestore_v1.types import Document
from google.oauth2 import service_account
from google.api_core.exceptions import InvalidArgument
from firestore.deploy_firestore import iter_seed
from backend.data import Repository

ANALYSIS = frozenset(('battery_meta', 'battery_models', 'battery_packs', 'battery_segments',
    'pdm_eda', 'pdm_files', 'pdm_meta', 'pdm_models', 'pdm_segments', 'supply_alerts',
    'supply_daily_history', 'supply_daily_summary', 'supply_dashboard_config',
    'supply_data_quality', 'supply_forecasts', 'supply_model_metrics', 'supply_part_metrics',
    'supply_parts', 'supply_walk_forward_metrics'))
OFFICIAL = 'manufacturingAi/manufacturing-ai'


def permitted(path):
    return (len(path.split('/')) % 2 == 0 and
            (path.split('/')[0] in ANALYSIS or path == OFFICIAL or path.startswith(OFFICIAL+'/')))


def client(key, project):
    credentials = service_account.Credentials.from_service_account_file(key)
    if credentials.project_id != project:
        raise ValueError('Credential project does not match requested project')
    return firestore.Client(project=project, credentials=credentials)


def fields(data):
    return json.loads(Document.to_json(Document(fields=_helpers.encode_dict(data))))['fields']


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def remap(value, source, target):
    if isinstance(value, list):
        return [remap(v, source, target) for v in value]
    if isinstance(value, dict):
        if set(value) == {'referenceValue'}:
            prefix = f'projects/{source}/databases/(default)/documents/'
            ref = value['referenceValue']
            if not ref.startswith(prefix) or not permitted(ref[len(prefix):]):
                raise ValueError('Reference outside migrated scope; manual review required')
            return {'referenceValue':ref.replace(prefix, f'projects/{target}/databases/(default)/documents/', 1)}
        return {k:remap(v, source, target) for k,v in value.items()}
    return value


def descendants(ref, snapshot=None):
    snap = snapshot if snapshot is not None else ref.get(timeout=30, retry=None)
    rows = []
    if snap.exists:
        rows.append({'path':ref.path, 'fields':fields(snap.to_dict())})
    for collection in ref.collections(timeout=30, retry=None):
        existing = {s.id:s for s in collection.stream(timeout=60, retry=None)}
        # list_documents includes missing ancestors with existing descendants.
        for child in collection.list_documents(timeout=30, retry=None):
            rows.extend(descendants(child, existing.get(child.id)))
    return rows


def create_batch(db, entries):
    batch = db.batch()
    for path, value in entries:
        batch.create(db.document(path), value)
    try:
        batch.commit(timeout=60, retry=None)
    except InvalidArgument as error:
        # Server transaction limits also include index work, which cannot be
        # predicted from serialized document bytes. Only this atomic rejection
        # is split; ambiguous timeouts are never blindly retried.
        if 'transaction too big' not in str(error).lower() or len(entries) < 2:
            raise
        middle = len(entries)//2
        create_batch(db, entries[:middle])
        create_batch(db, entries[middle:])


def export(args):
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=False)
    db = client(args.key, args.project)
    rows = []
    for collection in db.collections(timeout=30, retry=None):
        if collection.id not in ANALYSIS:
            continue
        existing = {s.id:s for s in collection.stream(timeout=90, retry=None)}
        refs = list(collection.list_documents(timeout=30, retry=None))
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(descendants, ref, existing.get(ref.id)) for ref in refs]
            part = []
            for done in as_completed(futures):
                part.extend(done.result())
        rows.extend(part)
        print(json.dumps({'exported':collection.id,'documents':len(part)}), flush=True)
    source_official = descendants(db.document(OFFICIAL))
    (folder/'source-official.json').write_text(json.dumps(source_official, ensure_ascii=False), encoding='utf-8')
    repo = Repository()  # validates the local seed before including it
    local = [{'path':r['path'], 'fields':fields(r['data'])} for r in iter_seed(ROOT/'firestore/seed')]
    rows.extend(local)
    rows.sort(key=lambda r:r['path'])
    if len({r['path'] for r in rows}) != len(rows) or not all(permitted(r['path']) for r in rows):
        raise ValueError('Unexpected export scope or duplicate path')
    payload = ''.join(canonical(row)+'\n' for row in rows).encode()
    (folder/'analysis.jsonl').write_bytes(payload)
    previous = {r['path']:r['fields'] for r in source_official}
    manifest = {'sourceProject':args.project, 'dataVersion':repo.manifest['dataVersion'],
                'documents':len(rows), 'sha256':hashlib.sha256(payload).hexdigest(),
                'counts':dict(Counter(r['path'].split('/')[0] for r in rows)),
                'officialLocalDocuments':len(local), 'officialSourceDocuments':len(previous),
                'officialSourceDifferences':sum(previous.get(r['path']) != r['fields'] for r in local),
                'excluded':'Employee identities, authentication, conversations, login logs, staff records and verification fixtures'}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False),flush=True)


def restore(args):
    folder=Path(args.folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    raw=(folder/'analysis.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
        raise ValueError('Backup hash mismatch')
    if args.project == manifest['sourceProject']:
        raise ValueError('Writing to the source project is prohibited')
    rows=[json.loads(line) for line in raw.decode().splitlines()]
    if len(rows)!=manifest['documents'] or not all(permitted(r['path']) for r in rows):
        raise ValueError('Backup scope mismatch')
    expected={r['path']:remap(r['fields'],manifest['sourceProject'],args.project) for r in rows}
    db=client(args.key,args.project)
    def read(paths):
        actual={}
        for start in range(0,len(paths),100):
            actual.update({s.reference.path:fields(s.to_dict()) for s in db.get_all(
                [db.document(p) for p in paths[start:start+100]],timeout=60,retry=None) if s.exists})
        return actual
    before=read(list(expected))
    conflicts=[p for p,v in before.items() if v != expected[p]]
    missing=sorted(expected.keys()-before.keys())
    (folder/'target-before.json').write_text(json.dumps(before,ensure_ascii=False),encoding='utf-8')
    plan={'targetProject':args.project,'sourceProject':manifest['sourceProject'], 'documents':len(expected),
          'create':len(missing),'unchanged':len(before)-len(conflicts),'conflicts':conflicts,'mode':'apply' if args.apply else 'dry-run'}
    (folder/'target-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    print(json.dumps(plan),flush=True)
    if conflicts:
        raise ValueError('Target has different existing documents; nothing was written')
    if not args.apply:
        return
    # Create-only commits refuse concurrent inserts; partial progress is safe to resume.
    entries=[]; size=0; written=0
    for path in missing:
        doc=Document.from_json(json.dumps({'fields':expected[path]}))
        value=_helpers.decode_dict(doc.fields, db)
        entries.append((path,value))
        size+=len(canonical(expected[path]).encode())
        if len(entries)>=100 or size>=500_000:
            create_batch(db,entries); written+=len(entries)
            print(json.dumps({'created':written,'total':len(missing)}),flush=True)
            entries=[];size=0
    if entries:
        create_batch(db,entries)
    after=read(list(expected))
    different=[p for p in expected if after.get(p)!=expected[p]]
    result={**plan,'verified':not different,'matching':len(expected)-len(different),'different':different,
            'expectedDigest':digest(expected),'actualDigest':digest(after)}
    (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if different:
        raise ValueError('Post-copy comparison failed; connection settings must not switch')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export','restore'])
    parser.add_argument('--key',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--folder',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    (export if args.action=='export' else restore)(args)
