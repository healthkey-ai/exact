-- Standalone DDL for the EXTERNALLY-MANAGED trials database.
--
-- EXACT's DB router (exact/db_router.py: allow_migrate) returns False for the
-- `trials` app whenever TRIALS_DATABASE_URL is set, so Django migration
-- trials/0007_alter_trial_benefit_score_and_more.py records itself as applied
-- but does NOT execute its AddConstraint DDL against the split trials DB.
-- Apply this script manually (or via the external schema pipeline) so the
-- 0–20 component-score invariant is enforced in production too.
--
-- Mirrors `python manage.py sqlmigrate trials 0007`. The AlterField steps in
-- that migration are no-op DDL (field validators + help_text only); the three
-- CHECK constraints below are the only real schema change.
--
-- Scale source of truth: trials.constants.TRIAL_SCORE_MAX (= 20).

-- Pre-flight: list any rows that would violate the new constraints. This must
-- return zero rows before the ALTER TABLE statements can succeed. Each CHECK
-- rejects values outside [0, 20], so test both bounds — an externally managed
-- DB that never enforced PositiveIntegerField's lower bound may hold negatives.
SELECT id, code, benefit_score, patient_burden_score, risk_score
FROM trials_trial
WHERE benefit_score < 0 OR benefit_score > 20
   OR patient_burden_score < 0 OR patient_burden_score > 20
   OR risk_score < 0 OR risk_score > 20;

-- Idempotent apply: skip each constraint if it already exists.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trials_benefit_score_0_20') THEN
        ALTER TABLE "trials_trial" ADD CONSTRAINT "trials_benefit_score_0_20"
            CHECK (("benefit_score" IS NULL OR ("benefit_score" >= 0 AND "benefit_score" <= 20)));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trials_patient_burden_score_0_20') THEN
        ALTER TABLE "trials_trial" ADD CONSTRAINT "trials_patient_burden_score_0_20"
            CHECK (("patient_burden_score" IS NULL OR ("patient_burden_score" >= 0 AND "patient_burden_score" <= 20)));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trials_risk_score_0_20') THEN
        ALTER TABLE "trials_trial" ADD CONSTRAINT "trials_risk_score_0_20"
            CHECK (("risk_score" IS NULL OR ("risk_score" >= 0 AND "risk_score" <= 20)));
    END IF;
END $$;
