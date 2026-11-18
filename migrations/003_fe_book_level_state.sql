-- 003: align with the FE contract (book-level state). Apply after 002_admin_request_state.sql.
-- Review and back up first; MySQL 8 DDL auto-commits. Run with the mysql client WITHOUT --force
-- so the script stops at the first error. NEVER run against production without approval.

SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'STRICT_ALL_TABLES');

-- 1) ISBN must exist for every book (FE uses it as the book id). No values are invented:
--    if any row has NULL isbn, the ALTER below fails and the script stops. Fix those rows by hand.
SELECT COUNT(*) AS requested_books_with_null_isbn FROM requested_books WHERE isbn IS NULL;
ALTER TABLE requested_books MODIFY isbn VARCHAR(13) NOT NULL;

-- 2) Report books whose requests had different request-level states (merged by the rule below).
SELECT COUNT(*) AS books_with_mixed_request_status FROM (
  SELECT requested_book_id FROM book_requests GROUP BY requested_book_id HAVING COUNT(DISTINCT status) > 1
) mixed;

-- 3) Book metadata and wider reject reason.
ALTER TABLE requested_books
  MODIFY reject_reason VARCHAR(500) NULL,
  ADD COLUMN pub_date DATE NULL,
  ADD COLUMN description TEXT NULL;

-- 4) Merge request-level states into the book-level state.
--    Rule (migration-only decision, not in FE): purchased > approved > pending > rejected.
UPDATE requested_books b
JOIN (
  SELECT requested_book_id,
         CASE WHEN SUM(status = 'PURCHASED') > 0 THEN 'purchased'
              WHEN SUM(status = 'APPROVED') > 0 THEN 'approved'
              WHEN SUM(status = 'PENDING') > 0 THEN 'pending'
              ELSE 'rejected' END AS merged
  FROM book_requests GROUP BY requested_book_id
) x ON x.requested_book_id = b.id
SET b.status = x.merged;
UPDATE requested_books b
SET b.reject_reason = (
  SELECT r.reject_reason FROM book_requests r
  WHERE r.requested_book_id = b.id AND r.status = 'REJECTED'
  ORDER BY r.processed_at DESC, r.id DESC LIMIT 1)
WHERE b.status = 'rejected';
UPDATE requested_books SET reject_reason = NULL WHERE status <> 'rejected';

-- 5) Notifications: point at the book, keep the reason, let request_id be cleared on cancel.
ALTER TABLE notifications
  ADD COLUMN requested_book_id BIGINT NULL,
  ADD COLUMN reason VARCHAR(500) NULL;
UPDATE notifications n
JOIN book_requests r ON r.id = n.request_id
SET n.requested_book_id = r.requested_book_id,
    n.reason = CASE WHEN n.type = 'rejected' THEN r.reject_reason ELSE NULL END;
-- Fails (NOT NULL) if a notification could not be mapped to a book; nothing is guessed.
ALTER TABLE notifications
  MODIFY requested_book_id BIGINT NOT NULL,
  ADD INDEX ix_notification_book (requested_book_id),
  ADD CONSTRAINT fk_notification_book FOREIGN KEY (requested_book_id) REFERENCES requested_books(id);
-- request_id becomes nullable with ON DELETE SET NULL (a student can cancel a request that already has
-- notifications). MySQL rejects dropping and re-adding a foreign key of the same name in one ALTER, so split.
ALTER TABLE notifications DROP FOREIGN KEY fk_notification_request;
ALTER TABLE notifications
  MODIFY request_id BIGINT NULL,
  ADD CONSTRAINT fk_notification_request FOREIGN KEY (request_id) REFERENCES book_requests(id) ON DELETE SET NULL;

-- 6) Librarian processing history (approve/reject/purchase only; reverts are not logged).
CREATE TABLE review_logs (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  requested_book_id BIGINT NOT NULL,
  status VARCHAR(16) NOT NULL,
  actor_id BIGINT NOT NULL,
  created_at DATETIME NOT NULL,
  INDEX ix_review_log_book (requested_book_id),
  INDEX ix_review_log_actor (actor_id),
  INDEX ix_review_log_created (created_at),
  CONSTRAINT fk_review_log_book FOREIGN KEY (requested_book_id) REFERENCES requested_books(id),
  CONSTRAINT fk_review_log_actor FOREIGN KEY (actor_id) REFERENCES users(id),
  CONSTRAINT ck_review_log_status CHECK (status IN ('approved','rejected','purchased'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Legacy: book_requests.status/reject_reason/processed_by/processed_at/price are kept (no data loss,
-- rollback possible) but no longer read or written by the application.
