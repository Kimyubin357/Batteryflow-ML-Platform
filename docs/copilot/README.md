# Copilot 현재 근거 문서

2026-11-05 기준으로 Copilot의 기존 검색 근거 9개를 현재 문서 5개로 교체했다. 검색 허용 목록은 `backend/copilot.py`의 `SOURCES`다. 과거 시드 안내·혼합 버전 README·연구 보고서 원본은 검색 목록에서 제거했다. 연구 재현용 원본은 저장소 이력으로 남으며 Copilot의 현재 근거 API에서 제공하지 않는다.

| 출처 ID | 파일 | 범위 |
|---|---|---|
| service-policy | service-policy.md | 공식 v5 자료, 운영 모델, 답변·기록 범위 |
| demand-validation | demand-validation.md | audited_v3 평가와 개선 미확정 |
| demand-contract | demand-contract.md | 날짜 정렬, CSV 입력과 현재 한계 |
| maintenance-current | maintenance.md | 현재 용접 이상 탐지와 검증 범위 |
| quality-current | quality.md | 저장 팩 분석, 시험 모델, 진단 한계 |

이 목록 안내 자체는 검색하지 않는다. 새 근거 문서에는 현재 정책만 담고, 변경 이력·과거 성능 주장은 별도 연구 기록에서 관리한다. 화면 수치는 문서에 고정하지 않고 질문 대상의 SCREEN 조회 결과를 사용한다.

문서 교체 후 서버를 재시작해야 실행 중인 메모리 인덱스가 바뀐다. 첫 검색에서 문서 ID·제목·본문과 임베딩 모델의 서명이 기존 캐시와 다르면 `.local/rag/chunks.json`과 `documents.faiss`를 새 근거로 덮어쓴다. 모델 다운로드 캐시까지 삭제할 필요는 없다. 공개 배포에는 이 폴더와 변경된 backend를 포함하고 새 리비전을 배포해야 한다. `scripts/portfolio_bundle.py`는 이 폴더를 포함한다.

검사: `python -B -m unittest backend.tests.test_copilot_sources backend.tests.test_copilot backend.tests.test_copilot_search`. 실제 로컬 검색 검사는 `python -B scripts/evaluate_copilot.py`다. `--live`는 Gemini를 호출하므로 로컬 검색 검사와 구분한다. 새 문서로 바뀌어도 기존 대화의 저장된 답변·인용은 과거 대화 기록으로 남는다.
