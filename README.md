# BookBinder

Python + Flask 기반 백엔드 초기 프로젝트입니다.

## 프로젝트 구조

```text
BookBinder/
├── app/
│   ├── __init__.py   # 앱 생성 및 설정
│   └── routes.py     # API 엔드포인트
├── .env.example     # 환경변수 예시
├── .gitignore       # Git 업로드 제외 항목
├── requirements.txt # 설치할 패키지
└── README.md
```

## 설치부터 실행까지 (Windows PowerShell)

프로젝트 폴더에서 다음 명령을 실행합니다. Python이 설치되어 있어야 합니다.

```powershell
# 1. Python 설치 확인
python --version

# 2. 프로젝트 전용 가상환경 생성 (최초 한 번)
python -m venv .venv

# 3. Flask 및 환경변수 로딩 패키지 설치
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. 로컬 환경변수 파일 생성 (최초 한 번)
Copy-Item .env.example .env

# 5. 개발 서버 실행
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

가상환경의 Python을 직접 실행하므로 활성화나 PowerShell 실행 정책 변경이 필요 없습니다.
이미 `.venv`와 `.env`를 만들었다면 2번과 4번은 건너뜁니다.
종료는 `Ctrl+C`입니다. 개발 서버와 디버그 모드는 로컬 개발에만 사용합니다.

브라우저에서 다음 주소를 확인합니다.

| 주소 | 결과 |
| --- | --- |
| http://127.0.0.1:5000/ | 환영 메시지 JSON |
| http://127.0.0.1:5000/api/health | 서버 상태 JSON |

상태 API의 응답:

```json
{"status": "ok", "service": "BookBinder"}
```

5000번 포트가 사용 중이라면 실행 명령 끝에 `--port 5001`을 추가합니다.

## 환경변수

`.env`는 개인 설정을 보관하는 파일이며 Git에 올라가지 않습니다.
로그인이나 세션 기능을 추가하기 전, 다음 명령으로 값을 생성해서 `.env`의
`SECRET_KEY=` 뒤에 붙여 넣으세요. 현재 기본 API에는 비밀 키가 필요하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

## GitHub에 처음 업로드하기

GitHub에서 비어 있는 저장소를 생성합니다. README, .gitignore, 라이선스 자동 생성은 선택하지 않습니다.
그다음 프로젝트 폴더에서 아래 명령을 실행합니다.

```powershell
git init -b main
git add .
git status
git commit -m "chore: initialize Flask backend"

# 아래 주소를 본인의 GitHub 저장소 주소로 교체하세요.
git remote add origin https://github.com/YOUR_USERNAME/BookBinder.git
git push -u origin main
```

커밋 전에 `git status`로 파일을 확인하세요. `.venv`, `.env`, Python 캐시는
`.gitignore`에 의해 제외됩니다. GitHub 로그인은 로컬 Git 인증 절차를 따릅니다.
커밋 시 작성자 정보 오류가 나오면 아래 값을 본인 정보로 설정한 뒤 다시 커밋합니다.

```powershell
git config user.name "본인 이름"
git config user.email "GitHub에 등록한 이메일"
```

## 다음 개발

API는 `app/routes.py`에 추가할 수 있습니다. 기능이 늘어나면 도서, 사용자 등 기능별로
Blueprint 파일을 나누면 됩니다. 데이터베이스와 로그인은 요구사항이 정해진 뒤 추가합니다.

참고: [Flask 설치](https://flask.palletsprojects.com/en/stable/installation/),
[Flask 빠른 시작](https://flask.palletsprojects.com/en/stable/quickstart/)
