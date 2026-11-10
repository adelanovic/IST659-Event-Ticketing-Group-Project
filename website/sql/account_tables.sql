-- Login tables the website expects, as SQL Server DDL. Matches website/db.py.
-- Reference for the team's UP/DOWN file: run after the Ticketmaster tables
-- (venues must exist), and drop in reverse order in DOWN.

CREATE TABLE dbo.accounts (
    account_id     INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_accounts PRIMARY KEY,
    email          NVARCHAR(255) NOT NULL CONSTRAINT UQ_accounts_email UNIQUE,   -- stored lowercase
    password_hash  VARCHAR(255)  NOT NULL,   -- Argon2id hash, e.g. $argon2id$v=19$m=65536,t=3,p=4$...
    account_type   VARCHAR(10)   NOT NULL
        CONSTRAINT CK_accounts_account_type CHECK (account_type IN ('customer', 'venue')),
    created_at     DATETIME2     NOT NULL CONSTRAINT DF_accounts_created_at DEFAULT SYSUTCDATETIME(),
    last_login_at  DATETIME2     NULL,
    is_active      BIT           NOT NULL CONSTRAINT DF_accounts_is_active DEFAULT 1
);

CREATE TABLE dbo.customers (
    customer_id  INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_customers PRIMARY KEY,
    account_id   INT NOT NULL
        CONSTRAINT FK_customers_account REFERENCES dbo.accounts(account_id)
        CONSTRAINT UQ_customers_account UNIQUE,                  -- one customer per account
    first_name   NVARCHAR(50) NOT NULL,
    last_name    NVARCHAR(50) NOT NULL,
    phone        VARCHAR(20)  NULL
);

-- A venue account manages one venue. NULL = nobody has claimed it yet.
ALTER TABLE dbo.venues ADD account_id INT NULL
    CONSTRAINT FK_venues_account REFERENCES dbo.accounts(account_id);

-- Filtered unique index: one venue per account, while many venues stay NULL.
-- (A plain UNIQUE constraint in SQL Server allows only a single NULL.)
CREATE UNIQUE INDEX UX_venues_account_id ON dbo.venues(account_id) WHERE account_id IS NOT NULL;
GO

-- DOWN, in this order:
-- DROP INDEX UX_venues_account_id ON dbo.venues;
-- ALTER TABLE dbo.venues DROP CONSTRAINT FK_venues_account;
-- ALTER TABLE dbo.venues DROP COLUMN account_id;
-- DROP TABLE dbo.customers;
-- DROP TABLE dbo.accounts;
