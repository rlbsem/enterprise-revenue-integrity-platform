CREATE TABLE schema_version(version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (1);
CREATE TABLE accounts(tenant text NOT NULL,entity text NOT NULL,id text PRIMARY KEY,body jsonb NOT NULL);
CREATE TABLE contracts(tenant text NOT NULL,entity text NOT NULL,id text PRIMARY KEY,body jsonb NOT NULL);
CREATE TABLE events(tenant text NOT NULL,entity text NOT NULL,id text PRIMARY KEY,body jsonb NOT NULL);
CREATE TABLE opportunities(tenant text NOT NULL,entity text NOT NULL,id text PRIMARY KEY,body jsonb NOT NULL);
