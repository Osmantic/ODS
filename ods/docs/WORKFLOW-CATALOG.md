# Workflow Catalog CLI

ODS ships an n8n workflow catalog under `config/n8n/catalog.json`. The dashboard
uses that catalog to display and enable workflows; headless operators can inspect
the same product-owned metadata through the `ods` CLI:

```bash
ods workflow list
ods workflow list --category voice
ods workflow search rag
ods workflow show document-qa
```

Add `--json` to any command for automation. Search matches workflow IDs, names,
descriptions, categories, and dependency service IDs.

These commands are read-only. They report catalog metadata and do not claim that
dependencies are healthy or that a workflow is installed in n8n. Runtime status
and enable/disable operations remain owned by the authenticated dashboard API so
they use its existing dependency checks and mutation lifecycle.
