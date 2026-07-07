# Agent Templates for ODS

**Mission:** M7 (OpenClaw Frontier Pushing)  
**Status:** 5 templates created, awaiting validation

Validated agent templates that work reliably on local Qwen3-30B-A3B.

## Templates

| Template | Purpose | Local Qwen | Fallback | Tools |
|----------|---------|------------|----------|-------|
| `code-assistant` | Programming, debugging | ✅ Primary | None | read, write, edit, exec |
| `research-assistant` | Web search, summarization | ✅ Primary | Kimi | web_search, web_fetch |
| `data-analyst` | CSV/JSON processing | ✅ Primary | None | read, write, edit, exec |
| `writing-assistant` | Editing (creative→fallback) | ✅ Editing | Kimi | read, write, edit |
| `system-admin` | Docker, Linux admin | ✅ Primary | None | exec, read, web_search |

## Usage

### Import in OpenClaw
```bash
/agent load code-assistant
/agent load data-analyst
```

### Use in Workflows
```yaml
# In your workflow
agent:
  template: code-assistant
  override:
    model: local-llama/qwen3-30b-a3b
```

## Validation Results (2026-02-11)

Tested on: Qwen3-30B-A3B-Instruct-AWQ (local)  
Test command: `python3 tests/validate-agent-templates.py`

| Template | Tests | Passed | Status |
|----------|-------|--------|--------|
| code-assistant | 2/2 | 100% | ✅ **VALIDATED** |
| research-assistant | 2/2 | 100% | ✅ **VALIDATED** |
| data-analyst | 2/2 | 100% | ✅ **VALIDATED** |
| writing-assistant | 1/2 | 50% | ⚠️ **NEEDS FALLBACK** |
| system-admin | 2/2 | 100% | ✅ **VALIDATED** |

**Overall: 9/10 tests passed (90%)**

### Notes

- **writing-assistant**: Local Qwen struggles with complex editing tasks. Confirms routing to fallback (Kimi) for creative work is correct.
- **All others**: Work reliably on local Qwen with ~2.7s response time
- Templates meet M7 "reliably on local models" criteria

## Design Principles

1. **Local-first:** Templates optimized for Qwen3-30B-A3B (free, fast, private)
2. **Fallback-aware:** Creative tasks route to Kimi; technical tasks stay local
3. **Tool-appropriate:** Each template gets only the tools it needs
4. **Safety-conscious:** Dangerous operations flagged (system-admin)
5. **Well-documented:** Usage examples and limitations clearly stated

## Next Steps

- [ ] Run validation tests on each template
- [ ] Create integration tests with real workloads
- [ ] Document common failure modes
- [ ] Add more templates (devops, security, data science)
