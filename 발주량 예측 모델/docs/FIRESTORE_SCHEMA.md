# 현재 공식 업로드 구조

공식 업로드 입력은 루트 `firestore/seed`의 **2026-11-05.v5 / schemaVersion 3 / 275문서**입니다. [현재 시드 계약](../../firestore/SEED_V5.md)을 사용하세요. 발주 모델은 audited_v3이며 XGBoost 예측을 기존 D+3 계획과 비교합니다. CV는 7일, 마지막 6일 성능은 이미 관측한 기간의 회고 평가입니다. 기존 계획 대비 개선은 미확정입니다.

아래 평면 구조는 연구용 내보내기 파일 설명입니다. 현재 공식 시드와 중복 업로드하지 않으며, 아래 저장안은 구현 완료된 기능을 뜻하지 않습니다. CSV 업로드 결과는 공식 시드나 검토 기록으로 자동 저장되지 않습니다.

---

# Firestore 준비 데이터 구조

`outputs/dashboard_data/firestore`의 JSONL 파일은 한 줄이 Firestore 문서 한 개다. 아직 Firestore에는 업로드하지 않는다. 각 문서의 `document_id`를 Firestore 문서 ID로 사용한다.

## 컬렉션

| 컬렉션 | 용도 | 주요 조회 키 |
|---|---|---|
| dashboard_config | 현재 모델, 예측 기간, 화면 설정 | document_id=current |
| parts | 부품 목록, 데이터 범위, 권장 모델 | part_number |
| daily_history | 부품별 일별 실제·계획 수량 | part_number, date |
| forecasts | 최종 테스트 실제값과 모델별 예측 | part_number, target_date |
| model_metrics | 전체 모델 성능 | Model |
| part_metrics | 부품별 모델 성능 | part_number, Model |
| daily_summary | 날짜별 전체 수량과 예측 합계 | target_date |
| alerts | 계획량과 권장 예측의 차이가 큰 최신 부품 | alert_type, target_date |
| data_quality | 데이터 품질 및 전처리 건수 | metric |
| walk_forward_metrics | fold별 반복검증 결과 | Fold, Model |

## 권장 인덱스

- `daily_history`: part_number 오름차순 + date 오름차순
- `forecasts`: part_number 오름차순 + target_date 오름차순
- `part_metrics`: part_number 오름차순 + MAE 오름차순
- `alerts`: alert_type 오름차순 + target_date 내림차순
- `walk_forward_metrics`: Model 오름차순 + Fold 오름차순

## 값 규칙

- 날짜와 시각은 UTC ISO 8601 문자열로 저장한다.
- NaN과 무한대는 JSON `null`로 변환한다.
- Part Number는 숫자로 변환하지 않고 문자열로 유지한다.
- 실제 수량과 계획 수량의 0은 정상값으로 유지한다.
- 예측 수량은 0 이상으로 제한한다.
- `alerts`는 재고 부족 확정 경보가 아니다. 재고량과 조달 리드타임이 없으므로 계획 대비 예측 차이를 보여주는 확인 대상이다.

## 화면과 컬렉션 연결

- 상단 KPI: forecasts, model_metrics
- 부품 선택 목록: parts
- 실제·예측 추이: forecasts 또는 daily_summary
- 부품별 성능표: part_metrics
- 계획 대비 확인 대상: alerts
- 데이터 품질 화면: data_quality
- 모델 안정성 화면: walk_forward_metrics
- 과거 입력 조회: daily_history

## 추론 결과 저장안

과거 설계안에서는 `inference_results` 컬렉션과 `part_number`, `origin_date`, `target_date`, `xgboost_prediction`, `moving_average_3d`, `recommended_model`, `recommended_forecast`, `model_version`, `created_at` 필드를 제안했다. 현재 서비스의 CSV 추론 결과를 DB에 기록하는 기능으로 구현됐다는 의미는 아니다. 공식 분석 대상에 대한 검토 기록은 별도 경로로 저장한다.
