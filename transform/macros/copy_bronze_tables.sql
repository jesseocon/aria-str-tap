{% macro copy_bronze_tables(source_project, dest_project, dataset, tables) %}
  {% if execute %}
    {% set create_dataset %}
      create schema if not exists `{{ dest_project }}.{{ dataset }}`
      options(location="US")
    {% endset %}
    {% do run_query(create_dataset) %}
    {% for table_name in tables %}
      {% set exists_query %}
        select count(*) as n
        from `{{ source_project }}.{{ dataset }}.INFORMATION_SCHEMA.TABLES`
        where table_name = '{{ table_name }}'
      {% endset %}
      {% set exists = run_query(exists_query) %}
      {% if exists and (exists.columns[0].values()[0] | int) > 0 %}
        {# COPY cannot change partitioning or clustering on an existing table. #}
        {% set drop_sql %}
          drop table if exists `{{ dest_project }}.{{ dataset }}.{{ table_name }}`
        {% endset %}
        {% do run_query(drop_sql) %}
        {% set copy_sql %}
          create table `{{ dest_project }}.{{ dataset }}.{{ table_name }}`
          copy `{{ source_project }}.{{ dataset }}.{{ table_name }}`
        {% endset %}
        {% do run_query(copy_sql) %}
        {{ log("copied bronze " ~ source_project ~ "." ~ dataset ~ "." ~ table_name ~ " -> " ~ dest_project, info=true) }}
      {% else %}
        {{ log("skip missing bronze table " ~ source_project ~ "." ~ dataset ~ "." ~ table_name, info=true) }}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endmacro %}
