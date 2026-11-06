-- Apply after 001_book_requests.sql. Review/backup first; MySQL 8 DDL auto-commits.
-- Historical request prices cannot be reconstructed from the current book price.
ALTER TABLE requested_books MODIFY isbn VARCHAR(13) NULL;
ALTER TABLE book_requests
  ADD COLUMN price INT NULL,
  ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  ADD COLUMN reject_reason VARCHAR(500) NULL,
  ADD COLUMN processed_by BIGINT NULL,
  ADD COLUMN processed_at DATETIME NULL,
  ADD INDEX ix_request_processed_by (processed_by),
  ADD CONSTRAINT fk_request_processed_by FOREIGN KEY (processed_by) REFERENCES users(id),
  ADD CONSTRAINT ck_request_price CHECK (price IS NULL OR price >= 0),
  ADD CONSTRAINT ck_request_status CHECK (status IN ('PENDING','APPROVED','REJECTED','PURCHASED'));
-- Preserve existing visible state. Actor/time cannot be reliably recovered.
UPDATE book_requests r JOIN requested_books b ON b.id = r.requested_book_id
SET r.status = UPPER(b.status), r.reject_reason = CASE WHEN b.status = 'rejected' THEN b.reject_reason ELSE NULL END;
-- price remains NULL for existing rows; no fabricated price snapshot.
-- requested_books.status/reject_reason are legacy fields; admin no longer writes them.
