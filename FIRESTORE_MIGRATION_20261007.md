# 개인 Firestore 이전 기록

2026-10-07 사용자 요청으로 팀 프로젝트 `ls-proejct-team1`의 분석 데이터를 개인 프로젝트 **`ls-battery-project`**로 복사했다. 사용자가 Firebase 콘솔에서 `(default)` 데이터베이스를 서울 `asia-northeast3`, 프로덕션 모드로 생성했다. API로 Firestore Native 모드와 위치를 확인했다.

## 이전 결과

분석 컬렉션 20개, 하위 문서 포함 **7,843문서**를 생성했다. 다시 전부 읽어 타입을 유지한 데이터와 비교했고 **7,843문서 일치, 불일치 0**이었다. 원본 팀 DB에서는 읽기만 했으며 수정·삭제하지 않았다. 대상에 다른 내용의 문서가 있으면 덮어쓰지 않고 중단하는 방식으로 실행했다.

| 범위 | 문서 수 | 의미 |
|---|---:|---|
| manufacturingAi | 275 | 현재 공식 분석 v4. 로컬 최종 시드가 기준이며 팀 원격과도 일치 |
| battery_packs | 306 | 팩·공정 102건 + heatmap/series 하위 문서 204건 |
| battery_meta / battery_models / battery_segments | 48 | 기존 품질 설정·모델 설명·분석 구간 |
| pdm_* | 23 | 기존 예지보전 자료, pdm_files 하위 문서 포함 |
| supply_* | 7,191 | 과거 공급망 자료·이력·모델 지표도 보존 |

과거 supply/pdm/battery 모델 자료를 복사한 것이 현재 공식 모델 정책을 바꾼다는 뜻은 아니다. 앱의 공식 분석 계약은 `2026-09-27.v4 / 275문서`다. 모델 재학습, 품질 결과 재추론, 임계값 재산출은 하지 않았다.

직원 계정, Firebase Authentication 사용자, 직원 권한, 개인 대화, 로그인 로그, 업무 기록과 검증용 임시 기록은 복사하지 않았다. 포트폴리오의 분석 화면에 필요한 범위와 과거 분석 자료를 이전한 것이다.

## 앱 연결

- `.env`의 FIREBASE_PROJECT_ID를 `ls-battery-project`로 변경했다.
- 제공받은 SDK JSON을 `.local/credentials/ls-battery-project-adminsdk.json`에 동일 내용으로 보관하고 그 상대 경로를 설정했다. 원래 제공 파일과 팀 SDK는 삭제하지 않았다.
- `BATTERYFLOW_MODE=portfolio`, `PORTFOLIO_DATA_SOURCE=firestore`로 설정했다.
- 방문자 세션·Copilot 대화·횟수 제한은 기존 로컬 SQLite에서 관리한다. 분석 조회만 본인 Firestore를 사용하므로 Firebase Authentication 없이 접속한다.
- DB 이전 시 기존 팀 FIREBASE_WEB_API_KEY를 비웠다. 후속 로그인 검증에서 개인 프로젝트에 웹 앱을 등록하고 해당 프로젝트의 키와 관리자 custom claim을 설정했다. 개인 DB와 팀 인증 프로젝트를 섞지 않는다. 새 설치의 인증·권한 설정은 [실행 안내](GETTING_STARTED.md)를 따른다.
- Gemini 키는 사용자 승인으로 연결했던 기존 값을 유지했다. Firestore 이전은 Gemini 키의 소유 프로젝트를 이전하는 작업이 아니다.

Firestore 분석 자료는 로컬 계약과 버전·내용이 일치해야 채택한다. 연결 실패·버전 불일치 시 로컬 자료로 자동 대체하지 않는다. 같은 정책의 원본 측정 파일과 모델 실행 자료는 계속 로컬 runtime을 사용한다.

## 검증

- 실제 개인 Firestore를 읽는 앱 API에서 통합 현황, 공급망, 예지보전, 품질 팩 목록·상세가 모두 HTTP 200이었다.
- analysisSource: `firestore / ready / ls-battery-project / 275문서` 확인.
- 품질 팩 102건과 표본 팩의 셀 176개 확인.
- 방문자는 visitor로 인증하고 실제 업무 기록 접근은 403으로 차단됨을 확인.
- 임시 방문자의 Copilot 한도 5회 유지. 이 API 검증에서는 Gemini를 호출하지 않았다.
- 재시작한 실제 포트폴리오 서버에서도 가입 없는 진입 후 공급망·예지보전·품질 화면을 1920×1080 브라우저에서 확인했다. 세 화면 로딩과 Copilot 잔여 횟수 표시가 정상이고 브라우저 오류는 0건이었다. API 메타데이터의 프로젝트가 ls-battery-project인지 함께 대조했다.
- 이전·조회 관련 자동 검사: 최초 30개 통과, 원격 포트폴리오 경로 추가 후 관련 21개 통과, 배치 분할 보완 후 이전 도구 4개 통과. 중복 실행 개수를 합산한 성능 지표로 사용하지 않는다.

## 이관 중 해결한 문제

첫 쓰기 묶음에서 Firestore의 `Transaction too big` 오류가 났다. 본문 직렬화 크기만으로 서버의 전체 처리 크기를 예측할 수 없었다. 처리 묶음 기준을 줄이고, 이 오류처럼 원자적으로 거절됐음이 명확한 경우에만 묶음을 둘로 나누어 다시 생성하도록 보완했다. 시간 초과처럼 반영 여부가 불명확한 오류에는 자동 재시도하지 않는다. 재실행 시 기존 동일 문서는 건너뛰고 다른 내용은 중단한다.

## 복구·근거

- 원본 분석 백업, 공식 원격 사본, 원래 연결 설정: `.local/firestore-migration-20261007/`.
- 백업은 Firestore 타입(큰 정수·날짜·바이트·문서 참조)을 보존한 JSONL이며 SHA-256을 검사한다. 서비스 계정 키는 백업 데이터 파일에 넣지 않는다. 별도의 `settings-before.env`에는 이전 연결 비밀값이 있으므로 공개하지 않는다.
- 재현 도구: `scripts/migrate_personal_firestore.py`. export는 원본 읽기 전용, restore 기본값은 dry-run, `--apply`만 대상 생성. 원본 프로젝트로 restore하는 동작은 거부한다.
- 공개 가능한 요약: [이전 검증 결과](portfolio_notes/maintenance/firestore_migration_20261007.json).
- `.env`, `.local/credentials`, 원본 백업은 Git 추적 제외 상태를 확인했다. GitHub 업로드는 수행하지 않았다.

새 서버에 배포할 때 개인 SDK 또는 해당 서버의 비밀 설정과 방문자 SQLite 지속 저장소를 함께 준비해야 한다. Firestore 문서가 서버의 모델 파일·원본 측정 파일·방문자 SQLite까지 대체하지는 않는다.
