select 'shell' as component, 'SQLPage' as title;
select 'title' as component, 'Database schema' as contents;
select 'text' as component,
       'Tables and views in this project database. Edit index.sql and add SQL pages to build your application.' as contents;
select 'table' as component, true as search, true as sort;
select name, type, sql as definition
from sqlite_schema
where type in ('table', 'view') and name not like 'sqlite_%'
order by name;
