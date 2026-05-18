-- Recreate exchange_rates with explicit source/target currency pair.
-- Old table stored rates implicitly anchored to RSD.
-- New table stores rate as: 1 source_currency * rate = target_currency amount.

DROP TABLE IF EXISTS exchange_rates;

CREATE TABLE exchange_rates (
    date             DATE NOT NULL,
    source_currency  TEXT NOT NULL,
    target_currency  TEXT NOT NULL,
    rate             DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (date, source_currency, target_currency)
);
