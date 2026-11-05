# 통합 Firestore 배포

이 디렉터리는 발주량 예측, 배터리 용접 예지보전, 배터리 품질보증, 통합 현황 화면에 필요한 데이터를 하나의 Firestore 구조로 생성하고 배포한다.

현재 공식 로컬 시드는 **`2026-11-05.v5` / schemaVersion 3 / 275개 문서**다. 발주 audited_v3의 XGBoost 예측과 기존 D+3 계획 비교를 반영했다. 개인 프로젝트 `ls-battery-project`에 반영한 뒤 **2026-11-05 11:34 KST에 275개 전체 일치**를 검증했다. 이는 그 시점의 검증 기록이며 현재 서버의 실시간 상태는 별도로 조회해야 한다.
현재 변경 내역과 계약은 [SEED_V5.md](SEED_V5.md), 배포 이력은 [DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md)를 확인한다. 과거 예지보전 변경은 [SEED_V4.md](SEED_V4.md), 과거 발주 v2 필드 설명은 [SEED_V3.md](SEED_V3.md)에 보존했다. 이전 259개 문서는 `versions/seed-v1`에 있다.

Copilot 근거 5개로의 교체는 로컬 코드 변경이며 아직 라이브에 배포하지 않았다. 이번 정리는 문서 수정만 수행했고 Firestore·Cloud Run은 변경하지 않았다.

## 데이터 구조

```text
manufacturingAi/{workspaceId}
  overview/current
  actions/{actionId}
  demandOverview/current
  demandConfig/current
  demandParts/{partId}
  demandModels/{modelId}
  maintenanceOverview/current
  maintenanceConfig/current
  maintenanceRuns/{runId}
    events/{eventId}
    measurementChunks/{chunkId}
  maintenanceModels/{modelId}
  maintenanceDataQuality/{fileId}
  qualityOverview/current
  qualityConfig/current
  qualityTests/{testId}
  qualityModels/{modelId}
  qualityDataQuality/{fileId}
```

원본 데이터는 서로 병합하지 않는다. `overview/current`와 `actions`만 세 트랙의 상태 및 조치 정보를 공통 형식으로 요약한다.

Spark 요금제의 문서 쓰기 수를 줄이기 위해 발주 이력은 부품 문서의 배열로, 용접 측정값은 100개 단위 청크로, 품질 시계열은 시험별 최대 300개 표시점으로 저장한다.

## 1. 시드 생성

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  '.\firestore\build_unified_seed.py'
```

## 2. 관리자 인증

서비스 계정 JSON은 저장소 밖에 보관한다. JSON 내용을 채팅이나 Git에 올리지 않는다.

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = 'C:\secure\firebase-service-account.json'
```

서비스 계정에는 최소 `Cloud Datastore User (roles/datastore.user)` 권한이 필요하다.

## 3. 읽기 전용 비교와 백업

로컬 `.local/settings.json`의 기존 프로젝트·서비스 계정 경로를 사용합니다. 인증 프로젝트와 지정 대상이 일치해야 합니다. 공식 워크스페이스만 읽으며 다른 루트의 문서는 조회하지 않습니다.

```powershell
.\.venv\Scripts\python.exe firestore/sync_official_seed.py --project-id YOUR_PROJECT_ID
```

## 4. 공식 데이터 변경분 동기화 (권장)

신규·변경 문서만 쓰며 폐기 문서 정리는 보관된 이전 공식 시드에 존재하는 경로로 제한합니다. 알 수 없는 추가 문서는 보존합니다. 반영 전 `.local/firestore-deployments`에 백업하고 반영 후 모든 공식 문서를 비교합니다. 500개 변경·9MB를 넘는 경우 자동 분할하지 않고 쓰기 전에 중단합니다.

```powershell
.\.venv\Scripts\python.exe firestore/sync_official_seed.py --project-id YOUR_PROJECT_ID --apply
```

기존 `deploy_firestore.py --replace-seed-roots`는 삭제 후 재적재하는 과거 방식입니다. 최신 일반 배포에는 위 동기화 도구를 사용하세요.

## 5. 데이터베이스 전체 교체 (비권장)

대상 프로젝트 ID를 두 번 일치시켜야 실행된다.
시드와 무관한 컬렉션이 있으면 기본적으로 중단되며, 정말 전체 삭제할 때만
`--allow-delete-unrelated-collections`를 추가한다.

```powershell
& '.\.venv\Scripts\python.exe' '.\firestore\deploy_firestore.py' `
  --project-id 'YOUR_PROJECT_ID' `
  --database-id '(default)' `
  --apply `
  --replace-all `
  --allow-delete-unrelated-collections `
  --confirm-project-id 'YOUR_PROJECT_ID'
```

관리자 SDK는 Firestore Security Rules를 우회하고 IAM으로 인증한다. 따라서 현재 규칙이 `allow read, write: if false;`여도 관리자 적재는 가능하다. 브라우저가 Firestore를 직접 읽는 구조라면 별도로 인증·보안 규칙을 설계해야 한다.

전체 삭제는 되돌릴 수 없다. 배포 도구는 먼저 대상 프로젝트, 데이터베이스 ID,
현재 문서 수, 시드와 무관한 컬렉션을 출력한다. 프로젝트 ID가 이중 확인되고
관련 없는 컬렉션 삭제까지 명시적으로 허용된 경우에만 전체 삭제한다.
