# 도서·신청·알림·관리자 연결 안내 (FE 동작 기준 계약)

BookBinder-FE(`main` d87ce56 + `fix/2-student-registration`의 학생 전용 가입·학번 5자리)의 동작을 기준으로 맞춘 계약이다. FE와 BE가 충돌하면 BE를 FE에 맞춘다. 상세 계약은 `openapi.yaml`.
실제 서비스 배포 준비가 된 상태는 아니다.

## 계약 요약

- JSON 키 camelCase, 상태(`pending|approved|rejected|purchased`)·역할(`student|librarian`) 소문자, 시간은 UTC epoch ms 정수, 모든 id는 문자열.
- 도서 id = 정규화된 ISBN-13. **처리 상태는 도서 단위**(`requested_books.status`)이며 한 책을 신청한 모든 학생에게 같이 적용되고 모두에게 알림이 간다.
- 반려된 책은 누구도 다시 신청할 수 없고, 구매완료된 책은 교내 소장으로 취급한다(되돌리면 해제).
- 오류: `{"error": {"code", "message", "fields"?}}`, 메시지는 FE 문구 그대로.
- 날짜 경계(기간 필터, 학기, 이번 주, 월별)는 Asia/Seoul 기준.

## 인증

서버 세션 쿠키(HttpOnly, SameSite=Lax) + CSRF 헤더(`X-CSRF-Token`) + 비밀번호 해시 + 역할 검사는 그대로다.
`GET /api/auth/csrf`와 `POST /api/auth/login`이 `csrfToken`을 돌려준다. 학생 전용 공개 가입(학번 숫자 5자리, 학년/반 `^[1-3]-\d{1,2}$`, 아이디 영문·숫자 4~20자, 비밀번호 8자 이상).
사서 계정은 운영자가 `flask --app app:create_app auth create-librarian <아이디> --name <이름>`으로 발급한다.
`PATCH /api/me/password`로 비밀번호를 바꾸면 현재 세션 외 같은 사용자의 다른 세션은 삭제된다.

## CORS

`CORS_ORIGINS`(쉼표 구분, 정확히 일치하는 출처만, `*` 불가)에 FE 출처를 넣는다(예: `http://localhost:5173`). 비우면 CORS 헤더를 붙이지 않는다.
쿠키는 SameSite=Lax라 FE와 BE가 같은 사이트일 때만 동작한다. 배포 주소가 다른 사이트면 `SameSite=None; Secure`와 출처 설정을 다시 결정해야 한다.

## 도서 데이터 연결

`BOOK_PROVIDER`, `LIBRARY_CATALOG` 환경변수는 `none`(기본, 503) 또는 `fixture`이다. **fixture는 개발 전용**(FE 샘플 24권, `app/books/fixture_data/books.json`)이며 `FLASK_DEBUG=1`일 때만 켜지고 운영에서는 거부된다. 실제 외부 공급자와 DLS 소장 파일 반입은 접근 정보가 없어 구현하지 않았다.

- `app.config['BOOK_PROVIDER']`: `search(query, page, page_size)` → `{items: [...]}`, `get(isbn)`. 항목은 `isbn, title, author, publisher, price, cover_url, pub_date(YYYY-MM-DD), description`.
- `app.config['LIBRARY_CATALOG']`: `ownership(isbn)`(`OWNED|NOT_OWNED|UNKNOWN`)와 `books()`(소장 목록 전체). `CatalogSnapshot`이 둘 다 제공한다.
- 소장 확인 불가(UNKNOWN/미설정)는 신청을 막는다(fail closed).
- 전체 도서의 범위: 공급자 검색 결과(검색어가 있을 때) + 교내 소장 목록 + 이미 신청된 책.

## DB

`migrations/003_fe_book_level_state.sql`(000~002 다음, 백업·검토 후, `mysql` 클라이언트로 `--force` 없이 실행). 운영 DB에는 승인 없이 적용하지 않는다.
- 책 단위 상태를 기준으로 되돌림. 신청별 상태가 섞인 책은 purchased > approved > pending > rejected 우선순위로 합친다(FE에 없는 이전용 결정). 섞인 책 수가 실행 중 출력된다.
- `requested_books.isbn`이 NULL인 행이 있으면 중단된다(값을 만들지 않는다).
- `pub_date`, `description`, `reject_reason VARCHAR(500)` 추가. `notifications`에 `requested_book_id`, `reason` 추가, `request_id`는 NULL 허용 + `ON DELETE SET NULL`. `review_logs` 신규.
- `book_requests`의 `status/reject_reason/processed_by/processed_at/price`는 남기되 더 이상 읽거나 쓰지 않는다(legacy).

## 테스트

`python -m unittest discover -s tests -v` (먼저 requirements.txt 설치). 테스트는 SQLite(외래 키 활성)와 fixture 공급자를 사용하며 실제 MySQL의 DDL·잠금·`ON DELETE SET NULL`을 대신하지 못한다.
