# 도서·신청·알림 연결 안내

현재 B 영역의 검색, 상세, 신청, 내 신청 목록, 알림 목록과 읽음 API의 초기 구현이다. 실제 서비스 배포 준비가 된 상태는 아니다.

## 인증 연결

회원가입·로그인·로그아웃·CSRF·내 정보 API가 구현되어 있다. 인증 미들웨어는 서버 측 세션에서 `g.current_user` 및 `g.csrf_valid`를 설정한다. 회원가입은 학생만 가능하다. 쿠키는 HttpOnly, SameSite=Lax이며 운영 HTTPS에서는 `SESSION_COOKIE_SECURE=true`로 설정해야 한다. 로그인과 로그아웃 후 `/api/auth/csrf`에서 새 토큰을 발급받는다. 운영 공개 전 로그인 요청 제한, 세션 만료 레코드 정리, 실제 MySQL 및 HTTPS/CORS 설정 검증이 필요하다.

## 도서 데이터 연결

`app.config['BOOK_PROVIDER']`에 `search(query, page, page_size)`와 `get(isbn)`을 제공하는 객체를 넣는다. `get`은 미발견 시 None, 발견 시 `title`, `author`, `publisher`, `price`, `cover_url`을 포함하는 dict를 반환한다. 가격은 원 단위 정수 또는 None이다. 외부 오류는 `ProviderError`, 타임아웃은 `ProviderTimeout`, 미설정은 `ProviderUnavailable`이다. 실제 제공처와 응답 정규화는 미구현이다.

`app.config['LIBRARY_CATALOG']`에 `search(query, page, page_size)`와 `ownership(isbn)`을 제공하는 객체를 넣는다. ownership은 `OWNED`, `NOT_OWNED`, `UNKNOWN` 중 하나를 반환한다. 미설정 또는 UNKNOWN은 신청을 허용하지 않는다. 검증된 행을 받아 검색과 소장 여부를 판정하는 `app.books.catalog.CatalogSnapshot`을 제공한다. ISBN 없는 소장 행이 하나라도 있으면 일치하지 않는 ISBN의 소장 여부를 UNKNOWN으로 처리하여 신청을 차단한다. 실제 DLS 파일 반입은 미구현이다.

검색 provider는 Notion API 명세의 items/page/page_size/has_next 형식, 교내 catalog는 items/page/page_size/total/catalog_updated_at 형식으로 반환한다. 데이터 접근 제어는 공급자에 위임하지 않는다.

## DB와 관리자 연동

`migrations/000_auth.sql`과 `001_book_requests.sql`은 새 MySQL 8 데이터베이스용 초안이다. 운영 DB 적용 전 백업·검토 및 실제 MySQL 검증이 필요하다. A의 관리자 상태 변경은 해당 `requested_books` 행을 잠그고 상태 변경, 해당 신청자별 notifications 생성, 변경 이력을 하나의 트랜잭션에서 처리해야 한다. 현재 관리자 처리와 알림 생성은 구현되지 않았다.

Notion API 명세의 snake_case 및 items 구조를 임시로 사용한다. 기존 개발 규칙 초안의 camelCase/공통 응답 포장과 충돌하므로 FE 연동 전 A와 최종 계약을 확정해야 한다. 모의 공급자 테스트는 실제 공급자나 MySQL 검증을 대체하지 않는다.

테스트: `python -m unittest discover -s tests -v` (먼저 requirements.txt 설치). 테스트는 SQLite와 모의 인증·제공처를 사용한다.
