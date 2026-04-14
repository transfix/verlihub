-- MySQL initialization script for Verlihub
-- Sets up the database with proper character set and test user permissions

-- Ensure proper character set
ALTER DATABASE verlihub CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Grant additional permissions for test user
GRANT ALL PRIVILEGES ON verlihub.* TO 'verlihub'@'%';

-- Create test database for integration tests
CREATE DATABASE IF NOT EXISTS verlihub_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON verlihub_test.* TO 'verlihub'@'%';

-- Increase connection timeout for testing (reduces "MySQL server gone" errors)
SET GLOBAL wait_timeout = 28800;  -- 8 hours
SET GLOBAL interactive_timeout = 28800;
SET GLOBAL max_connections = 200;

FLUSH PRIVILEGES;
