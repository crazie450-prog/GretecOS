-- Veins per dimension (WO-1.6, D128): how many, how many the player knows (client cache), how
-- many he marked depleted, and how many different mixes.
SELECT dim, count(*) AS veins, sum(known) AS known, sum(depleted) AS depleted,
  count(DISTINCT mix_id) AS mixes
FROM vein
GROUP BY dim
ORDER BY dim;
