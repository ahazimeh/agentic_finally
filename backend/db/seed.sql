-- Default seed data. Only applied when the default user profile does not exist yet,
-- so user changes (e.g. removing a default ticker) survive restarts.

INSERT INTO users_profile (id, cash_balance, created_at)
VALUES ('default', 10000.0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT INTO watchlist (id, user_id, ticker, added_at)
SELECT lower(hex(randomblob(16))), 'default', t.ticker, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM (
    SELECT 'AAPL' AS ticker UNION ALL SELECT 'GOOGL' UNION ALL SELECT 'MSFT'
    UNION ALL SELECT 'AMZN' UNION ALL SELECT 'TSLA' UNION ALL SELECT 'NVDA'
    UNION ALL SELECT 'META' UNION ALL SELECT 'JPM' UNION ALL SELECT 'V'
    UNION ALL SELECT 'NFLX'
) AS t;
