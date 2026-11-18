-- 003: align with the FE contract (book-level state). Apply after 002_admin_request_state.sql.
--
-- BEFORE YOU RUN (MySQL 8 DDL auto-commits, so this script cannot be rolled back):
--   1. Stop every write to the application (API and CLI) for the whole run.
--   2. Take a full backup (mysqldump of the BookBinder database) and keep it until the result is verified.
--   3. Run with the mysql client WITHOUT --force so the script stops at the first error.
--      NEVER run against production without approval.
-- IF IT FAILS HALFWAY: do NOT rely on ROLLBACK and do NOT re-run the script on the partly changed database
--   (later steps are not idempotent). Restore the backup taken in step 2, fix the cause, then run from the start.
-- NOTE: review_logs starts empty. The weekly counts (weekApproved/weekRejected) only include processing done
--   after this migration; earlier approvals/rejections cannot be rebuilt (processed_at keeps only the last value).

SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'STRICT_ALL_TABLES');

-- 0) Pre-flight, BEFORE the first DDL. Stops (SIGNAL) without changing anything when an ISBN is not already a
--    canonical ISBN-13. The API looks books up by the normalised ISBN-13, so an old ISBN-10 row (002 allowed them)
--    would not be found and the same book could be requested twice. Nothing is converted or deleted here: fix
--    the listed rows by hand while keeping their requests/notifications, e.g. UPDATE the isbn to its ISBN-13 when
--    no row uses it yet, or move book_requests/notifications/review references to the existing ISBN-13 row
--    (watch the UNIQUE(user_id, requested_book_id) rule) and then delete the empty old row.
DROP PROCEDURE IF EXISTS bb_003_preflight;
DELIMITER //
CREATE PROCEDURE bb_003_preflight()
BEGIN
  DECLARE null_isbn INT;
  DECLARE odd_isbn INT;
  SELECT COUNT(*) INTO null_isbn FROM requested_books WHERE isbn IS NULL;
  SELECT COUNT(*) INTO odd_isbn FROM requested_books WHERE isbn IS NOT NULL AND isbn NOT REGEXP '^97[89][0-9]{10}$';
  IF null_isbn > 0 OR odd_isbn > 0 THEN
    SELECT b.id, b.isbn, b.title, (SELECT COUNT(*) FROM book_requests r WHERE r.requested_book_id = b.id) AS requests
      FROM requested_books b WHERE b.isbn IS NULL OR b.isbn NOT REGEXP '^97[89][0-9]{10}$';
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '003 stopped: requested_books has NULL or non-ISBN-13 isbn rows (listed above). Clean them up by hand first.';
  END IF;
END//
DELIMITER ;
CALL bb_003_preflight();
DROP PROCEDURE bb_003_preflight;

-- 1) ISBN must exist for every book (FE uses it as the book id). The pre-flight above already guarantees it.
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
  created_at DATETIME(6) NOT NULL,  -- fractional seconds kept so week-boundary counts are exact
  INDEX ix_review_log_book (requested_book_id),
  INDEX ix_review_log_actor (actor_id),
  INDEX ix_review_log_created (created_at),
  CONSTRAINT fk_review_log_book FOREIGN KEY (requested_book_id) REFERENCES requested_books(id),
  CONSTRAINT fk_review_log_actor FOREIGN KEY (actor_id) REFERENCES users(id),
  CONSTRAINT ck_review_log_status CHECK (status IN ('approved','rejected','purchased'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Legacy: book_requests.status/reject_reason/processed_by/processed_at/price are kept (no data loss,
-- rollback possible) but no longer read or written by the application.
