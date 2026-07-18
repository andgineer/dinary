-- Remember which model created each llm-sourced classification rule, so a later
-- user correction can rate that model's quality (delayed negative feedback).
-- Nullable: existing rows and user-created rules stay NULL and are never rated.

ALTER TABLE classification_rules ADD COLUMN llm_name TEXT;
