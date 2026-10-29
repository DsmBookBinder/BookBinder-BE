-- Initial schema for a new MySQL 8 database. Store all DATETIME values in UTC.
CREATE TABLE users (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 login_id VARCHAR(50) NOT NULL UNIQUE,
 password_hash VARCHAR(255) NOT NULL,
 name VARCHAR(50) NOT NULL,
 role VARCHAR(16) NOT NULL DEFAULT 'student',
 grade_class VARCHAR(20) NULL,
 student_no VARCHAR(20) NULL,
 created_at DATETIME NOT NULL,
 CHECK (role IN ('student','librarian'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE auth_sessions (
 token_hash VARCHAR(64) NOT NULL PRIMARY KEY,
 csrf_hash VARCHAR(64) NOT NULL,
 user_id BIGINT NULL,
 expires_at DATETIME NOT NULL,
 INDEX ix_auth_sessions_user_id (user_id),
 INDEX ix_auth_sessions_expires_at (expires_at),
 CONSTRAINT fk_auth_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
