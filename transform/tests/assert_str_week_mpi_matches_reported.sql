{{ config(tags=['str']) }}

-- The week mart computes indexes as hotel / comp set x 100. STR also reports its own seven-day
-- MPI, ARI and RGI in the same Combined Summary block. They must agree (within rounding); a
-- mismatch means the pivot picked the wrong rows or the wrong day of the week.
WITH reported AS (
  SELECT
    key_date AS str_week_end,
    comp_set_id,
    MAX(IF(entity = 'index' AND metric = 'occupancy' AND measure = 'value', value * 100, NULL)) AS mpi,
    MAX(IF(entity = 'index' AND metric = 'adr' AND measure = 'value', value * 100, NULL)) AS ari,
    MAX(IF(entity = 'index' AND metric = 'revpar' AND measure = 'value', value * 100, NULL)) AS rgi
  FROM {{ ref('stg_mlt_str__series') }}
  WHERE source_view = 'combined_summary_weekly'
    AND block = 'CombinedSummaryTableData.totalCompSet'
    AND key_date IS NOT NULL
  GROUP BY str_week_end, comp_set_id, selected_date
  QUALIFY ROW_NUMBER() OVER (PARTITION BY str_week_end, comp_set_id ORDER BY MAX(data_as_of) DESC, selected_date DESC) = 1
)

SELECT w.str_week_end, w.mpi, r.mpi AS reported_mpi, w.ari, r.ari AS reported_ari, w.rgi, r.rgi AS reported_rgi
FROM {{ ref('mlt_mart__benchmark__week') }} AS w
JOIN reported AS r USING (str_week_end, comp_set_id)
WHERE ABS(w.mpi - r.mpi) > 0.5 OR ABS(w.ari - r.ari) > 0.5 OR ABS(w.rgi - r.rgi) > 0.5
