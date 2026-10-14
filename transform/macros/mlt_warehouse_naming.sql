{% macro mlt_stream_table_name(stream) -%}
  {{ var('source') }}__{{ stream }}_{{ var('property_id') }}
{%- endmacro %}

{% macro mlt_mart_table_name(suffix) -%}
  mlt_mart__{{ suffix }}_{{ var('property_id') }}
{%- endmacro %}
