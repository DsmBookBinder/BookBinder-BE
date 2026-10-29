-- Apply after 000_auth.sql against a new MySQL 8 database.
CREATE TABLE requested_books (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  isbn VARCHAR(13) NOT NULL UNIQUE,
  title VARCHAR(200) NOT NULL,
  author VARCHAR(100) NOT NULL,
  publisher VARCHAR(100) NOT NULL,
  price INT NULL,
  cover_url VARCHAR(500) NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  reject_reason VARCHAR(255) NULL,
  quantity INT NOT NULL DEFAULT 1,
  updated_at DATETIME NOT NULL,
  CHECK (price IS NULL OR price >= 0),
  CHECK (quantity >= 0),
  CHECK (status IN ('pending','approved','rejected','purchased'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE book_requests (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  requested_book_id BIGINT NOT NULL,
  requested_at DATETIME NOT NULL,
  CONSTRAINT uq_request_user_book UNIQUE (user_id, requested_book_id),
  INDEX ix_request_user (user_id),
  INDEX ix_request_book (requested_book_id),
  CONSTRAINT fk_request_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_request_book FOREIGN KEY (requested_book_id) REFERENCES requested_books(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE notifications (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  request_id BIGINT NOT NULL,
  type VARCHAR(16) NOT NULL,
  message VARCHAR(255) NOT NULL,
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL,
  INDEX ix_notification_user (user_id),
  INDEX ix_notification_request (request_id),
  CONSTRAINT fk_notification_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_notification_request FOREIGN KEY (request_id) REFERENCES book_requests(id),
  CHECK (type IN ('approved','rejected','purchased'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
