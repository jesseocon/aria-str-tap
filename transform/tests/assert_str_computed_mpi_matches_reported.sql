{{ config(tags=['str']) }}

-- The period mart computes indexes as hotel / benchmark x 100. For the comp set and a single
-- month, STR also reports MPI, ARI and RGI in its Monthly view. They must agree (within rounding);
-- a mismatch means the pivot picked the wrong rows.
WITH reported AS (
  SELECT
    key_date AS month_end,
    comp_set_id,
    MAX(IF(entity = 'index' AND metric = 'occupancy' AND measure = 'value', value * 100, NULL)) AS mpi,
    MAX(IF(entity = 'index' AND metric = 'adr' AND measure = 'value', value * 100, NULL)) AS ari,
    MAX(IF(entity = 'index' AND metric = 'revpar' AND measure = 'value', value * 100, NULL)) AS rgi
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'monthly' AND block = 'MonthlyTableData.compSet' AND key_date IS NOT NULL
  GROUP BY month_end, comp_set_id
)

SELECT p.month_end, p.mpi, r.mpi AS reported_mpi, p.ari, r.ari AS reported_ari, p.rgi, r.rgi AS reported_rgi
FROM {{ ref('mlt_mart__benchmark__period') }} AS p
JOIN reported AS r USING (month_end, comp_set_id)
WHERE p.benchmark = 'comp_set'
  AND p.window_name = 'month'
  AND (ABS(p.mpi - r.mpi) > 0.5 OR ABS(p.ari - r.ari) > 0.5 OR ABS(p.rgi - r.rgi) > 0.5)
