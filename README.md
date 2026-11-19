# BookBinder-BE

학교 도서관 희망 도서 신청 서비스 **BookBinder**의 백엔드(Flask + MySQL)입니다.
학생이 도서를 검색·신청하고, 사서가 신청 도서를 처리(승인·반려·구매완료)·예산 집계하는 API를 제공합니다.
프론트엔드: [BookBinder-FE](https://github.com/DsmBookBinder/BookBinder-FE) (FE 동작이 기능 명세이며, 충돌하면 BE를 FE에 맞춥니다.)

> 실제 서비스 배포 준비가 된 상태는 아닙니다. 외부 도서 공급자와 DLS 소장 파일은 아직 연결되어 있지 않습니다.

## 주요 기능

- **인증·회원**: 학생 전용 공개 가입, 로그인/로그아웃, 내 정보·비밀번호 변경. 서버 세션 쿠키 + CSRF 토큰 + 비밀번호 해시 + 역할 검사
- **도서**: 검색(전체/소장, 정확도순/신청 많은 순), 상세, 인기 도서
- **학생 신청**: 도서 신청, 내 신청 목록, 대기 중 신청 취소, 알림 목록·읽음·전체 읽음
- **사서**: 신청 도서 목록(필터·정렬·요약), 일괄 승인/반려/구매완료, 되돌리기, 가격·수량 수정, 신청 목록 엑셀, 예산 조회·엑셀, 처리 현황

## 계약 요약

- JSON 키는 camelCase, 상태(`pending|approved|rejected|purchased`)와 역할(`student|librarian`)은 소문자, 시간은 UTC epoch 밀리초, 모든 id는 문자열
- 도서 id는 정규화된 ISBN-13. **처리 상태는 도서 단위**(`requested_books.status`)로, 한 책을 신청한 모든 학생에게 함께 적용되고 알림이 갑니다.
- 반려된 책은 누구도 다시 신청할 수 없고, 구매완료된 책은 교내 소장으로 취급합니다.
- 오류 형식: `{"error": {"code", "message", "fields"?}}`
- 날짜 경계(기간 필터, 학기, 이번 주, 월별)는 Asia/Seoul 기준

전체 엔드포인트와 스키마는 [`openapi.yaml`](openapi.yaml), 연결 안내는 [`BACKEND_INTEGRATION.md`](BACKEND_INTEGRATION.md)를 참고하세요.

## API 개요

| 영역 | 엔드포인트 |
|---|---|
| 인증 | `GET /api/auth/csrf`, `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout` |
| 내 정보 | `GET·PATCH /api/me`, `PATCH /api/me/password` |
| 도서 | `GET /api/books`, `GET /api/books/popular`, `GET /api/books/{bookId}` |
| 학생 신청 | `POST /api/requests`, `GET /api/me/requests`, `DELETE /api/me/requests/{id}` |
| 알림 | `GET /api/me/notifications`, `PATCH /api/me/notifications/{id}/read`, `PATCH /api/me/notifications/read-all` |
| 사서 도서 | `GET /api/admin/books`, `POST /api/admin/books/status`, `POST /api/admin/books/revert`, `PATCH /api/admin/books/{bookId}/price`, `PATCH /api/admin/books/{bookId}/quantity`, `POST /api/admin/books/export` |
| 사서 예산·현황 | `GET /api/admin/budget`, `POST /api/admin/budget/export`, `GET /api/admin/summary` |
| 기타 | `GET /api/health` |

`/api/admin/*`은 사서만 호출할 수 있고, 쓰기 요청은 `X-CSRF-Token` 헤더가 필요합니다.

## 기술 스택

- Python, Flask 3.1, Flask-SQLAlchemy 3.1
- MySQL (PyMySQL), 테스트는 SQLite
- 엑셀은 외부 의존성 없이 표준 라이브러리로 생성

## 프로젝트 구조

```
app/
  __init__.py      앱 팩토리 (create_app)
  auth/            회원·세션·CSRF, 사서 계정 CLI
  books/           도서 검색·상세, 공급자·소장 목록, 개발용 fixture
  requests/        학생 신청·취소·알림
  admin/           사서 목록·상태 변경·예산·엑셀·처리 현황
  cors.py          설정된 출처만 허용하는 CORS
  serializers.py   camelCase·소문자·epoch ms 직렬화
  errors.py        오류 응답
migrations/        MySQL migration (000~003)
tests/             unittest
config.py          환경 설정
openapi.yaml       API 계약
```

## 시작하기

### 1. 설치

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 환경 변수

`.env.example`을 `.env`로 복사해 값을 채웁니다.

| 변수 | 설명 |
|---|---|
| `SECRET_KEY` | **필수.** 비어 있으면 앱이 시작되지 않습니다. `python -c "import secrets; print(secrets.token_hex(32))"`로 생성 |
| `DB_HOST` `DB_PORT` `DB_USER` `DB_PASSWORD` `DB_NAME` | MySQL 접속 정보 |
| `SESSION_COOKIE_SECURE` | HTTPS 운영 환경에서 `true` |
| `CORS_ORIGINS` | FE 출처(쉼표 구분, 정확히 일치만 허용, `*` 불가). 비우면 CORS 헤더를 붙이지 않음 |
| `BOOK_PROVIDER` `LIBRARY_CATALOG` | `none`(기본, 503) 또는 `fixture`(개발 전용) |

### 3. DB 준비

`migrations/`의 SQL을 번호 순서(000 → 003)로 적용합니다.

> **주의**: 003은 DDL이 자동 커밋되어 롤백되지 않습니다. 쓰기를 중단하고 전체 백업을 만든 뒤 `--force` 없이 실행하세요.
> 실패하면 백업을 복원하고 원인을 고쳐 처음부터 다시 실행합니다. 운영 DB에는 승인 없이 적용하지 않습니다.
> 자세한 내용은 [`BACKEND_INTEGRATION.md`](BACKEND_INTEGRATION.md)와 `migrations/003_fe_book_level_state.sql` 머리말을 참고하세요.

### 4. 실행

```bash
# 개발 서버 (fixture 공급자는 FLASK_DEBUG=1 에서만 켜집니다)
FLASK_DEBUG=1 BOOK_PROVIDER=fixture LIBRARY_CATALOG=fixture flask --app app:create_app run
```

상태 확인: `GET http://localhost:5000/api/health`

### 5. 사서 계정 발급

사서는 공개 가입이 없으며 운영자가 CLI로 발급합니다.

```bash
flask --app app:create_app auth create-librarian <아이디> --name <이름>
```

## 테스트

```bash
python -m unittest discover -s tests -v
```

테스트는 SQLite(외래 키 활성)와 fixture 공급자를 사용합니다. 실제 MySQL의 DDL·잠금·`ON DELETE SET NULL` 동작은 대신 검증하지 못합니다.

## 개발용 fixture 공급자

FE 샘플 도서 24권(`app/books/fixture_data/books.json`)을 도서 공급자·교내 소장 목록으로 사용합니다.
`FLASK_DEBUG=1`일 때만 켜지며, 운영 설정에서는 거부되고 해당 기능은 503을 반환합니다.

## 알려진 제한

- 실제 외부 도서 공급자와 DLS 소장 파일 반입은 미구현(접근 정보 없음)
- 운영 배포 주소가 FE와 다른 사이트이면 쿠키(`SameSite=None; Secure`)와 CORS 출처 재검토 필요
- 한국어 가나다순 정렬이 영문·숫자가 섞인 제목에서 브라우저 `localeCompare('ko')`와 다를 수 있음

## 관련 문서

- [`openapi.yaml`](openapi.yaml) — API 계약
- [`BACKEND_INTEGRATION.md`](BACKEND_INTEGRATION.md) — 도서·신청·알림·관리자 연결 안내
- [`docs/admin-implementation.md`](docs/admin-implementation.md) — 이전 신청 단위 관리자 API 구현 기록
- [`AGENTS.md`](AGENTS.md) — 팀 승인 전 제안 문서
