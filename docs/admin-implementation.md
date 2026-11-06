# A 관리자 API 구현 및 계약 변경

## 입력과 완료 조건
사용자가 채팅으로 제공한 API 명세와 endpoint 이미지를 이번 관리자 3개 API의 기준으로 사용한다.
GET /api/admin/requests, POST /api/admin/requests/export, PATCH /api/admin/requests/{id}/status.
사서 세션/쓰기 CSRF, 목록 필터, 상태 충돌, 원자적 알림 저장, 발주 검증 및 XLSX 수식 안전성을 테스트한다.

## 초안과 충돌 및 적용 기준
AGENTS.md는 미승인 제안이며 책 단위 상태/camelCase/data 포장을 제시한다.
이번에 사용자가 제공한 명세는 신청 단위 상태/대문자 상태/snake_case/items 응답이다.
관리자 3개는 사용자 제공 명세를 적용한다. ISBN별 묶음은 엑셀 옵션이며 상태 변경 단위는 신청 ID다.
현재 feat/a-auth-admin 작업 브랜치를 사용한다. 푸시 전 점검에서 origin fetch를 완료했다.
requirements.txt는 기존 고정 버전 그대로 복구했다. 앱이 참조하지 않는 중복 app/config.py는 사용자 확인 후 삭제했으며 루트 config.py를 유지한다.

## 수정 범위 및 공유 모델 영향
- app/admin/: 권한, 검증, 목록, 조건부 상태 갱신, 알림 생성, 메모리 XLSX.
- app/books/models.py: BookRequest에 price/status/reject_reason/processed_by/processed_at, 사용자 관계와 제약 추가. NULL ISBN 지원.
- app/requests/routes.py (B 영역): 새 신청은 제공처 정가를 신청 행에 저장하고 내 신청 목록/상태 필터는 신청 행의 상태를 읽는다. 관리자 변경이 학생 화면과 불일치하지 않도록 필요한 연결이다. B 담당자 리뷰 필요.
- app/__init__.py: 관리자 Blueprint 등록.
- migrations/002_admin_request_state.sql, openapi.yaml, tests/test_admin.py.

## DB 전환
기존 000, 001 migration은 수정하지 않는다. 백업 및 검토 후 MySQL 8 테스트 DB에 002를 순서대로 적용한다.
기존 신청의 상태/반려 사유는 책 상태에서 복사한다. 과거 담당자·처리 시각·신청 당시 가격은 복원 근거가 없으므로 NULL이다.
기존 신청의 NULL 가격은 발주 시 409로 차단된다. 현재 책 가격을 과거 가격인 것처럼 채우지 않는다.
새 신청부터 price 스냅샷을 저장한다. 관리자/학생 응답의 권위 있는 상태는 book_requests.status다.
requested_books.status/reject_reason은 이전 B 구현의 호환 필드로 남기며 관리자 변경을 복제하지 않는다.
기존 B 신규신청 로직의 책 단위 신청 제한은 이번 명세에 없는 정책이라 유지했다. 신청별 구매완료와 외부 소장목록 동기화 정책은 별도 합의가 필요하다.
NULL ISBN은 기존/반입 데이터용이다. 학생 ISBN 검증은 그대로 유지한다.

## FE 전환
id는 신청 ID다. 전체 예산은 모든 페이지를 모은 뒤 계산한다. 현재 페이지 합계를 전체 예산으로 표시하지 않는다.
목록 가격은 신청 스냅샷이다. 동일 ISBN 가격 불일치는 발주에서 409이며 화면도 조정 필요 상태로 표시해야 한다.
발주 요청은 request_ids와 명시적인 group_by_isbn boolean을 보내고 Blob으로 다운로드한다.
상태 변경에는 현재 화면의 expected_status를 보내고 409이면 재조회한다. 성공 이후 학생 목록과 알림을 재조회한다.

## 검증 및 제한
실행 명령: `.venv/Scripts/python.exe -m unittest discover -s tests -v`.
기존 의존성: Flask==3.1.3, python-dotenv==1.2.4, Flask-SQLAlchemy==3.1.1, PyMySQL[rsa]==1.2.3.
새 외부 의존성 없이 Python 표준 ZIP/XML로 XLSX를 생성한다. 파일 셀은 명시적 inlineStr/숫자이며 수식 노드를 만들지 않는다.
SQLite 테스트는 실제 MySQL의 DDL/동시 실행/행 잠금 검증을 대체하지 않는다. 운영 DB에는 migration을 적용하지 않는다.
로그인 제한, 제공처, 실제 DLS, 프론트 연동, 실 MySQL 검증은 이번 관리자 API 구현 완료와 별개다.

### 실행 결과 (2026-11-06)
- 전체 unittest 25개 통과 (관리자 9개 + 기존 16개).
- 앱 팩토리 생성 및 /api/health: 200, {status: ok, service: BookBinder}.
- git diff --check 통과.
- 기존 테스트에서 SQLite 연결 미종료 ResourceWarning이 발생하나 테스트 실패는 없음.
- 실제 MySQL/브라우저/Excel 앱 검증은 미실행. XLSX ZIP/XML 구조와 셀 타입을 자동 검증함.
- 푸시 전 전체 unittest 25개와 앱 팩토리·health 검사를 재실행하여 통과했다. OpenAPI 파일의 JSON 파싱 및 관리자 경로 3개를 확인했다(전체 OpenAPI 스키마 검증은 아님).
- Postman CLI가 설치되어 있지 않아 Postman 검증은 실행하지 않았다. 기존 unittest를 사용했다.
