# audited_v3 학습 기록 설명 정정 · 2026-11-05

이 파일은 완료된 학습 산출물의 설명 오류를 정정한다. 모델·예측값·평가지표·원본 파일·학습 코드는 바꾸지 않았다. `training_plan_v2.json`이라는 파일명은 생성기의 이전 명칭이며 내부 `version`과 실행 결과는 `audited_v3`다.

## 읽어야 할 실제 분할

| 항목 | JSON의 낡은 설명 문자열 | audited_v3 실제 설정·실행 |
|---|---|---|
| 외부 검증 | `selection`: three outer folds of four dates | 외부 2회, 유효 목표일 합계 7일 |
| 내부 검증 | `selection`: five-date inner validation | 각 구간 4일 |
| 마지막 평가 | `holdout`: last seven target dates | 회고 6일, 이전 실험에서 이미 관측한 기간 |
| 최소 학습 | 설명 문자열에 없음 | 최소 5일 |

실제 값은 [training_plan_v2.json](training_plan_v2.json)의 `split_sizes`에 각각 `folds=2`, `cv_days=7`, `validation_days=4`, `holdout_days=6`, `min_train_days=5`로 저장되어 있다. [split_audit.csv](source_total/split_audit.csv)의 실행 결과도 이에 맞는다. `selection`·`holdout`의 영어 설명은 이전 기본 설정이 고정 문자열로 남은 것이므로 실제 실행 설정으로 인용하지 않는다.

CV는 2021-10-14~10-21의 유효 7일·111개 부품·743건이다. 회고는 2021-10-25~11-01의 유효 6일·109개 부품·643건이며 새 독립 시험이 아니다. CV MAE와 CV+회고 13일의 일별 승리일수·bootstrap은 집계 범위가 다르다.

## 재현 기록의 보존

`training_plan_v2.json` 정정 전 원문의 SHA-256은 `da98f40a565b0413b42eb65c9dfef897d2910fc088b741e8fc24e0d3e02e305a`다. 원문은 그대로 두고 이 정정 파일을 함께 읽는다. 생성 원인은 `src/retrain_audited.py`의 설명 문자열이며, 사용자 요청으로 기능·학습 코드 변경은 보류했다.

[run_manifest.json](run_manifest.json)의 `firestore_uploaded=false`도 **학습 완료 당시** 상태다. 이후 `2026-11-05.v5` 시드 275개 반영·검증과 구분하며 현재 배포 기록은 [Firestore 배포 상태](../../../firestore/DEPLOYMENT_STATUS.md)를 따른다. 학습 당시 manifest와 해시를 현재 배포 상태에 맞춰 덮어쓰지 않는다.

현재 해석은 [발주 평가 근거](../../../docs/copilot/demand-validation.md), 입력 조건은 [CSV 계약](../../../docs/copilot/demand-contract.md)을 따른다. 두 문서의 교체는 로컬 완료이며, 이번 문서 정리에서 새 Cloud Run 배포를 실행한 것은 아니다.
