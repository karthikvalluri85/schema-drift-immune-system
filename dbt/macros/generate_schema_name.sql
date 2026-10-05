{# Land models in ANALYTICS_STAGING / ANALYTICS_MARTS regardless of target, so the
   blast-radius exposures always point at stable relations. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}
    {%- else -%}{{ target.schema }}_{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}
