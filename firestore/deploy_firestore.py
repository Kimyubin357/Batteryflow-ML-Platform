from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED_DIR = ROOT / "firestore" / "seed"
BATCH_SIZE = 50


def iter_seed(seed_dir: Path) -> Iterator[dict[str, Any]]:
    manifest = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        with (seed_dir / item["file"]).open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def validate_seed(seed_dir: Path) -> tuple[int, Counter[str]]:
    seen: set[str] = set()
    roots: Counter[str] = Counter()
    for item in iter_seed(seed_dir):
        path = item.get("path")
        if not isinstance(path, str) or len(path.split("/")) % 2:
            raise ValueError(f"잘못된 문서 경로: {path}")
        if path in seen:
            raise ValueError(f"중복 문서 경로: {path}")
        if set(item) != {"path", "data"}:
            raise ValueError(f"path/data 이외의 필드가 있습니다: {path}")
        size = len(json.dumps(item["data"], ensure_ascii=False).encode("utf-8"))
        if size >= 900_000:
            raise ValueError(f"문서 크기가 안전 한도를 넘습니다: {path}")
        seen.add(path)
        roots[path.split("/")[0]] += 1
    return len(seen), roots


def firestore_client(project_id: str, database_id: str):
    try:
        import google.auth
        from google.cloud import firestore
    except ImportError as exc:
        raise SystemExit("firebase-admin 또는 google-cloud-firestore를 먼저 설치하세요.") from exc
    credentials, credential_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if credential_project and credential_project != project_id:
        raise SystemExit(
            f"중단: 인증 프로젝트({credential_project})와 대상 프로젝트({project_id})가 다릅니다."
        )
    return firestore.Client(project=project_id, database=database_id, credentials=credentials)


def collection_documents(collection_ref):
    try:
        yield from collection_ref.list_documents(page_size=200)
    except (AttributeError, TypeError):
        for snapshot in collection_ref.stream():
            yield snapshot.reference


def count_document_tree(document_ref) -> int:
    total = 1
    for collection in document_ref.collections():
        for child in collection_documents(collection):
            total += count_document_tree(child)
    return total


def inventory(database) -> dict[str, int]:
    result: dict[str, int] = {}
    for collection in database.collections():
        count = 0
        for document_ref in collection_documents(collection):
            count += count_document_tree(document_ref)
        result[collection.id] = count
    return result


def delete_document_tree(document_ref, batch_state: dict[str, Any]) -> None:
    for collection in document_ref.collections():
        for child in list(collection_documents(collection)):
            delete_document_tree(child, batch_state)
    batch_state["batch"].delete(document_ref)
    batch_state["pending"] += 1
    batch_state["deleted"] += 1
    if batch_state["pending"] >= BATCH_SIZE:
        batch_state["batch"].commit()
        batch_state["batch"] = batch_state["database"].batch()
        batch_state["pending"] = 0


def delete_collections(database, collection_ids: set[str] | None = None) -> int:
    state = {"database": database, "batch": database.batch(), "pending": 0, "deleted": 0}
    for collection in database.collections():
        if collection_ids is not None and collection.id not in collection_ids:
            continue
        for document_ref in list(collection_documents(collection)):
            delete_document_tree(document_ref, state)
    if state["pending"]:
        state["batch"].commit()
    return int(state["deleted"])


def import_seed(database, seed_dir: Path) -> int:
    batch = database.batch()
    pending = 0
    written = 0
    for item in iter_seed(seed_dir):
        batch.set(database.document(item["path"]), item["data"])
        pending += 1
        written += 1
        if pending >= BATCH_SIZE:
            batch.commit()
            batch = database.batch()
            pending = 0
    if pending:
        batch.commit()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Firestore 전체 교체 배포")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--database-id", default="(default)")
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace-all", action="store_true")
    parser.add_argument("--replace-seed-roots", action="store_true")
    parser.add_argument("--confirm-project-id")
    parser.add_argument("--allow-delete-over-free-quota", action="store_true")
    parser.add_argument("--allow-delete-unrelated-collections", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS가 설정되지 않았습니다.")
    seed_count, roots = validate_seed(args.seed_dir)
    database = firestore_client(args.project_id, args.database_id)
    current = inventory(database)
    current_total = sum(current.values())
    seed_roots = set(roots)
    unrelated = {name: count for name, count in current.items() if name not in seed_roots}
    print(json.dumps({
        "targetProject": args.project_id,
        "databaseId": args.database_id,
        "currentCollections": current,
        "currentDocuments": current_total,
        "seedDocuments": seed_count,
        "seedTopLevelCollections": dict(roots),
        "unrelatedCollections": unrelated,
        "unrelatedDocuments": sum(unrelated.values()),
        "mode": "APPLY" if args.apply else "DRY_RUN",
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        return
    if args.replace_all == args.replace_seed_roots:
        raise SystemExit(
            "적재 범위를 정확히 하나 선택하세요: --replace-seed-roots 또는 --replace-all"
        )
    if args.confirm_project_id != args.project_id:
        raise SystemExit("--confirm-project-id 값이 대상 프로젝트 ID와 일치해야 합니다.")
    delete_count = current_total if args.replace_all else sum(
        current.get(name, 0) for name in seed_roots
    )
    if delete_count > 20_000 and not args.allow_delete_over_free_quota:
        raise SystemExit(
            "삭제 대상 문서가 20,000개를 넘어 Spark 일일 무료 삭제 한도를 초과할 수 있습니다. "
            "계속하려면 요금제/할당량을 확인한 뒤 --allow-delete-over-free-quota를 명시하세요."
        )
    if args.replace_all and unrelated and not args.allow_delete_unrelated_collections:
        raise SystemExit(
            "시드와 무관한 컬렉션이 존재하므로 전체 삭제를 중단했습니다. "
            "정말 모두 삭제하려면 --allow-delete-unrelated-collections를 명시하세요."
        )
    deleted = delete_collections(database, None if args.replace_all else seed_roots)
    written = import_seed(database, args.seed_dir)
    print(json.dumps({
        "deleted": deleted,
        "written": written,
        "scope": "all" if args.replace_all else "seed-roots",
        "status": "complete",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
