# 실행과 검증

## 1. 포트폴리오 둘러보기

Python 3.12를 설치한 뒤 README의 설치 명령을 실행합니다. Windows에서는 `setup_local.cmd`로 환경을 만들고 `start_portfolio.cmd`로 열 수도 있습니다.

`.env.example`을 `.env`로 복사하는 작업은 **최초 설정 때 한 번만** 합니다. 이미 설정이 있으면 덮어쓰지 마세요.

```dotenv
BATTERYFLOW_MODE=portfolio
PORTFOLIO_DATA_SOURCE=local
PORTFOLIO_GEMINI_API_KEY=여기에_본인의_키를_로컬에서_입력
```

실제 키는 `.env`에만 입력합니다. 키가 없으면 분석 화면과 CSV 예측은 이용할 수 있지만 Copilot 생성은 비활성화됩니다. FastEmbed 검색 모델은 최초 이용 시 다운로드되며 이후 `.local/rag`를 사용합니다.

## 2. 본인의 Firestore 연결

이미 적재한 개인 Firestore를 사용할 때 다음 값을 설정합니다.

```dotenv
FIREBASE_PROJECT_ID=본인의_프로젝트_ID
FIREBASE_SERVICE_ACCOUNT_FILE=.local/credentials/firebase-adminsdk.json
PORTFOLIO_DATA_SOURCE=firestore
```

서비스 계정 파일은 서버에만 둡니다. 공개 저장소에는 넣지 않습니다. 원격 공식 분석의 데이터 버전과 275문서 내용은 포함된 `firestore/seed`와 일치해야 합니다. 품질 팩 화면은 별도의 `battery_packs` 및 하위 자료를 읽습니다. 시드 275문서만 적재하면 팩 분석까지 복원되는 것은 아닙니다.

신규 이용자는 `local`로 먼저 실행할 수 있습니다. Firestore로 전환하면 연결 오류를 로컬 자료로 자동 대체하지 않습니다. 방문자 인증·사용량·대화는 두 데이터 소스 모두 로컬 SQLite에 저장합니다.

## 3. 직원·관리자 로그인

Firebase 콘솔에서 본인 프로젝트의 Authentication 이메일/비밀번호 로그인을 활성화하고 계정을 생성합니다. 프로젝트에 등록한 웹 앱의 API 키와 실제 Copilot용 Gemini 키를 `.env`에 설정합니다.

```dotenv
FIREBASE_WEB_API_KEY=본인의_웹_앱_API_키
GEMINI_API_KEY=본인의_Gemini_키
```

계정 생성만으로는 직원 화면에 접근할 수 없습니다. 서버의 Firebase Admin SDK로 해당 계정의 custom claims에 `manufacturingRole: employee` 또는 `manufacturingRole: admin`을 설정해야 합니다. 기존 claims가 있으면 병합해 보존합니다. 방문자는 이 권한을 부여받지 않습니다.

로그인 기록을 저장하려면 다음 명령으로 암호화 키를 생성한 뒤 로컬 설정에 보관합니다. 키는 기존 로그 복호화에 필요하므로 운영 중 임의로 바꾸지 않습니다.

```powershell
.venv\Scripts\python.exe -m backend.auth_log init-keys
.venv\Scripts\python.exe run_local.py --connected --port 8078
```

`http://127.0.0.1:8078/login`에서 본인 계정으로 로그인합니다. 직원 모드는 검토 기록을 실제 Firestore에 저장하므로 공개 포트폴리오 방문자에게 관리자 계정을 공유하지 않습니다.

## 4. 자동 검사

다음 검사는 학습이나 원격 데이터 재계산 없이 로컬 코드와 저장 자료를 확인합니다.

```powershell
.venv\Scripts\python.exe -B run_local.py --demo --check
.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -q
```

발주량 연구 검사는 별도 연구 의존성이 필요합니다. 원본 공급망 데이터, 저장된 예측과 모델을 비교하며 재학습하지 않습니다.

```powershell
.venv\Scripts\python.exe -m pip install -r "발주량 예측 모델/requirements.txt"
.venv\Scripts\python.exe -B -m unittest discover -s "발주량 예측 모델/tests" -p test_audited_v2.py -q
```

신뢰구간과 순위 근거를 다시 만들려면 `portfolio_notes/evidence/review_portfolio_evidence.py`를 실행합니다. 결과 JSON/CSV를 갱신하므로 비교할 기존 근거를 먼저 보존합니다. `retrain_audited.py`와 패키징·DB 이전 도구는 앱 실행에 필요하지 않으며 현재 산출물을 바꿀 수 있어 별도의 연구 작업에서 실행합니다.

## 5. 공개 서비스와 포트폴리오 모드 배포 조건

공개 서비스는 Google Cloud Run에서 운영합니다. [라이브 서비스 체험 안내](https://kimyubin357.github.io/batteryflow-live.html)에서 체험 계정을 확인한 뒤 로그인할 수 있습니다.

가입 없는 포트폴리오 모드를 직접 배포할 때는 HTTPS, 공개 호스트, 비밀 설정을 구성합니다. `PORTFOLIO_DB`는 재시작에도 유지되는 볼륨에 두고, 여러 독립 서버에 각각 SQLite 파일을 두는 구성은 사용하지 않습니다. 프록시의 실제 방문자 IP 전달과 신뢰 범위도 설정합니다.

자세한 방문자 제한·보존 기간은 [PORTFOLIO_RUN.md](PORTFOLIO_RUN.md)를 참고하세요. 공개용 묶음은 실행 자료와 발주 평가 근거를 포함합니다. 팀 연구의 대용량 원본, 과거 모델 중간 파일, 개인 학습 자료 전체는 포함하지 않아 과거 연구 경로 모두의 재실행을 보장하지는 않습니다.
