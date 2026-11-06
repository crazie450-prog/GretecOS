-- The newest export run in the DB (as gos db build orders runs when it prunes): the run the
-- recipe tools answer from. fingerprint '' means Play's fingerprint was never recorded (WO-1.4).
SELECT id, run_key, created_at, pack_version, gt_version, nei_version, fingerprint
FROM export_run
ORDER BY created_at DESC, run_key DESC
LIMIT 1;
