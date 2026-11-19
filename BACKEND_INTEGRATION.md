# 도서·신청·알림·관리자 연결 안내 (FE 동작 기준 계약)

BookBinder-FE(`main` d87ce56 + `fix/2-student-registration`의 학생 전용 가입·학번 5자리)의 동작을 기준으로 맞춘 계약이다. FE와 BE가 충돌하면 BE를 FE에 맞춘다. 상세 계약은 `openapi.yaml`.
실제 서비스 배포 준비가 된 상태는 아니다.

## 계약 요약

- JSON 키 camelCase, 상태(`pending|approved|rejected|purchased`)·역할(`student|librarian`) 소문자, 시간은 UTC epoch ms 정수, 모든 id는 문자열.
- 신청 가능한 도서 id는 정규화된 ISBN-13이다. ISBN 없는 교내 자료는 `dls-`로 시작하는 조회용 id와 `isbn: null`을 반환한다. **처리 상태는 도서 단위**(`requested_books.status`)이며 한 책을 신청한 모든 학생에게 같이 적용되고 모두에게 알림이 간다.
- 반려된 책은 누구도 다시 신청할 수 없고, 구매완료된 책은 교내 소장으로 취급한다. 되돌리기는 **구매 상태 때문에 생긴 소장 판정만** 해제한다. 교내 소장 목록에 있는 책은 `is_owned()`가 계속 `owned: true`를 반환한다.
- 오류: `{"error": {"code", "message", "fields"?}}`, 메시지는 FE 문구 그대로.
- 날짜 경계(기간 필터, 학기, 이번 주, 월별)는 Asia/Seoul 기준.

## 인증

서버 세션 쿠키(HttpOnly, SameSite=Lax) + CSRF 헤더(`X-CSRF-Token`) + 비밀번호 해시 + 역할 검사는 그대로다.
`GET /api/auth/csrf`와 `POST /api/auth/login`이 `csrfToken`을 돌려준다. 공개 가입은 학생 전용이다. 신청자의 학번은 `1407`처럼 4자리이며 백엔드가 `1-4`를 학년·반으로 저장한다. 기존 5자리 학번은 계정과 신청 이력에 보존된다. 아이디는 영문·숫자 4~20자, 비밀번호는 8~128자다.
관리자 계정은 운영자가 `flask --app app:create_app auth create-admin <아이디> --name <이름>`으로 발급한다. 내부 역할값 `librarian`은 유지한다. `POST /api/auth/admin/login`은 이 역할만 허용한다.
로그인과 비밀번호 변경은 같은 계정 행을 `FOR UPDATE`로 잠그고 최신 해시로 검증한다(변경이 먼저 끝나면 이전 비밀번호로 세션이 발급되지 않고, 로그인이 먼저 끝나면 변경 때 그 세션이 삭제된다). `SECRET_KEY`가 비어 있으면 앱이 시작되지 않는다.
`PATCH /api/me/password`로 비밀번호를 바꾸면 현재 세션 외 같은 사용자의 다른 세션은 삭제된다.

## 알림 쿼리 키 전환 (`unreadOnly`)

계약 키는 `unreadOnly`다. 전환 기간에는 이전 `unread_only`도 받으며(둘 다 오면 `unreadOnly` 우선), FE가 `unreadOnly`로 배포된 뒤 다음 릴리스에서 제거한다.

## CORS

`CORS_ORIGINS`(쉼표 구분, 정확히 일치하는 출처만, `*` 불가)에 FE 출처를 넣는다(예: `http://localhost:5173`). 비우면 CORS 헤더를 붙이지 않는다. 설정한 경우 모든 응답에 `Vary: Origin`을 붙이고, `Access-Control-Allow-Origin`은 허용 목록과 정확히 일치할 때만 붙인다.
쿠키는 SameSite=Lax라 FE와 BE가 같은 사이트일 때만 동작한다. 배포 주소가 다른 사이트면 `SameSite=None; Secure`와 출처 설정을 다시 결정해야 한다.

## 도서 데이터 연결

`BOOK_PROVIDER=kakao`와 `KAKAO_REST_API_KEY`를 서버 환경에 설정한다. 공급자 설정이 없고 키가 있으면 Kakao를 사용하고, 명시적으로 `BOOK_PROVIDER=none`을 설정하면 끈다. Kakao 응답의 `price`(정가)를 사용한다. **fixture는 개발 전용**(FE 샘플 24권)이며 `FLASK_DEBUG=1`에서만 동작한다.

### DLS 반입

1. DLS에서 반출한 원본 XLSX를 서버가 읽을 수 있는 비공개 경로에 보관한다(로컬에서는 `instance/dls-catalog.xlsx`).
2. `LIBRARY_CATALOG=dls`와 `DLS_CATALOG_XLSX` 절대 경로를 설정하고 앱을 시작한다. 시작할 때마다 `리스트 반출` 시트를 전부 검증해 메모리에 읽는다. 파일이 비어 있거나 열 구조 또는 값이 잘못된 경우 시작 실패로 표시한다. 기존 앱이 실행 중이면 새 파일을 먼저 별도 프로세스에서 검증하고 파일 교체 후 프로세스를 재시작한다.
3. 로그인한 계정에서 `GET /api/books?scope=owned&q=어린왕자`로 검색하고 응답의 `id`로 상세를 조회한다. 등록번호 원문은 응답에 넣지 않는다. 같은 등록번호·제목·저자·출판사·출판년도의 반복 행만 하나로 합친다. DLS 자료상태는 반출 당시 값이다.

받은 파일에는 ISBN 열이 없다. 지원하는 ISBN 포함 파일은 같은 시트/필수 열에 `ISBN` 열을 추가한 형식이다. 소장 목록에 미식별 자료가 있으면 목록과 일치하지 않는 외부 ISBN의 소장 여부는 `UNKNOWN`이다. 이런 책의 신청은 503으로 막는다. 프론트는 상세·검색에서 `ownership: UNKNOWN`이면 신청 버튼을 비활성화하고 소장 여부 미확인 안내를 표시해야 한다.

로컬 시연 서버는 개발 모드에서 `BOOKBINDER_DEMO_ASSUME_UNOWNED=true`를 설정할 수 있다. 별도 시연 DB를 사용한다. 이때 DLS 조회는 실제 자료를 그대로 보여주고, 카카오에서 조회한 유효 ISBN의 소장 여부가 미확인일 때만 신청을 허용한다. 확인된 교내 소장 도서와 구매완료 도서는 기존대로 신청할 수 없다. `/api/me`의 `X-BookBinder-Data: demo` 헤더로 화면에 시연 모드를 알린다.

- `app.config['BOOK_PROVIDER']`: `search(query, page, page_size)` → `{items: [...]}`, `get(isbn)`. 항목은 `isbn, title, author, publisher, price, cover_url, pub_date(YYYY-MM-DD), description`.
- `app.config['LIBRARY_CATALOG']`: `ownership(isbn)`(`OWNED|NOT_OWNED|UNKNOWN`)와 `books()`(소장 목록 전체). `CatalogSnapshot`이 둘 다 제공한다.
- 소장 확인 불가(UNKNOWN/미설정)는 신청을 막는다(fail closed).
- 전체 도서의 범위: 공급자 검색 결과(검색어가 있을 때) + 교내 소장 목록 + 이미 신청된 책.

### 학교 발주 양식

사서가 `POST /api/admin/books/export`에 기존 필터와 함께 `{"format":"school"}`을 보내면 학교 양식의 구입 예정 도서 목록을 내려받는다. 승인됐고 구매 수량이 1 이상인 책의 정가, 수량, 소계와 총합을 포함한다. 가격이 미정인 책이 포함된 경우 `409 PRICE_REQUIRED`를 반환한다. 기본 다운로드는 기존 사서용 신청 현황을 유지한다.

## DB

`migrations/003_fe_book_level_state.sql`(000~002 다음, `mysql` 클라이언트로 `--force` 없이 실행). 운영 DB에는 승인 없이 적용하지 않는다.
- **적용 절차**: 적용하는 동안 쓰기를 중단하고, 사전에 전체 백업을 만든다. DDL은 자동 커밋이라 ROLLBACK이 되지 않는다. 중간에 실패하면 ROLLBACK이나 처음부터 재실행에 기대지 말고 **백업을 복원한 뒤** 원인을 고치고 다시 실행한다.
- 첫 DDL 전에 `requested_books.isbn`이 NULL이거나 ISBN-13 정규형이 아닌 행(002가 허용한 ISBN-10 등)이 있으면 목록을 보여 주고 중단한다. 조용히 변환·삭제하지 않으며, 운영자가 신청·알림 참조를 보존한 채 정리한 뒤 다시 실행한다.
- `review_logs`는 비어 있는 채로 시작하므로 `/api/admin/summary`의 `weekApproved`/`weekRejected`는 003 적용 이후 처리만 센다(`created_at`은 `DATETIME(6)`).
- 책 단위 상태를 기준으로 되돌림. 신청별 상태가 섞인 책은 purchased > approved > pending > rejected 우선순위로 합친다(FE에 없는 이전용 결정). 섞인 책 수가 실행 중 출력된다.
- `pub_date`, `description`, `reject_reason VARCHAR(500)` 추가. `notifications`에 `requested_book_id`, `reason` 추가, `request_id`는 NULL 허용 + `ON DELETE SET NULL`. `review_logs` 신규.
- `book_requests`의 `status/reject_reason/processed_by/processed_at/price`는 남기되 더 이상 읽거나 쓰지 않는다(legacy).

## 테스트

`python -m unittest discover -s tests -v` (먼저 requirements.txt 설치). 테스트는 SQLite(외래 키 활성)와 fixture 공급자를 사용하며 실제 MySQL의 DDL·잠금·`ON DELETE SET NULL`을 대신하지 못한다.
