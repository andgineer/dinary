DROP TABLE IF EXISTS exchange_rates;

CREATE TABLE exchange_rates (
    currency TEXT NOT NULL,
    date     DATE NOT NULL,
    rate     DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (currency, date)
);
