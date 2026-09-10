"""Pure, reversible Pixel access-mode configuration changes.

Changes only five per-agent OpenClaw overrides and returns an external baseline
of their values and parent presence. Inputs and unrelated settings are preserved.
This module does not change files, services or effective host permissions.
"""

import copy

AGENT_ID = "pixel"

ENABLED_VALUES = {
    ("sandbox", "mode"): "off",
    ("tools", "exec", "host"): "gateway",
    ("tools", "exec", "security"): "full",
    ("tools", "exec", "ask"): "off",
    ("tools", "fs", "workspaceOnly"): False,
}

PATHS = list(ENABLED_VALUES.keys())
PATH_KEYS = [".".join(p) for p in PATHS]

# Allowed scalar types for leaf values, per dotted path.
LEAF_TYPES = {
    "sandbox.mode": str,
    "tools.exec.host": str,
    "tools.exec.security": str,
    "tools.exec.ask": str,
    "tools.fs.workspaceOnly": bool,
}

# OpenClaw access-mode schema values; full configuration validation belongs
# to the caller before an installed configuration is changed.
ENUMS = {
    "sandbox.mode": ("off", "non-main", "all"),
    "tools.exec.host": ("sandbox", "gateway", "node"),
    "tools.exec.security": ("deny", "allowlist", "full"),
    "tools.exec.ask": ("off", "on-miss", "always"),
}


class MigrationError(ValueError):
    """Missing/duplicate agents, malformed shapes, or invalid baseline.

    Always raised before any mutation; the caller's config is never partially
    changed. Messages are path-only: they never echo configuration values.
    """


def _validate_root(config):
    """Root-shape checks with path-only messages (never echoes values)."""
    if not isinstance(config, dict):
        raise MigrationError("malformed config: root must be a JSON object")
    agents = config.get("agents")
    if not isinstance(agents, dict):
        raise MigrationError("malformed config: root.agents must be an object")
    if not isinstance(agents.get("list"), list):
        raise MigrationError("malformed config: root.agents.list must be an array")


def _select_agent(config):
    entries = config["agents"]["list"]
    matches = [e for e in entries if isinstance(e, dict) and e.get("id") == AGENT_ID]
    if not matches:
        raise MigrationError(
            "malformed config: no root.agents.list entry with id '%s'" % AGENT_ID)
    if len(matches) > 1:
        raise MigrationError(
            "malformed config: duplicate root.agents.list entries with id '%s'" % AGENT_ID)
    return matches[0]


def _validate_shapes(agent):
    """Ensure no non-dict blocks any changed path (checked before mutating)."""
    for path in PATHS:
        node = agent
        walked = []
        for key in path[:-1]:
            walked.append(key)
            if key in node:
                if not isinstance(node[key], dict):
                    raise MigrationError(
                        "malformed config: non-object at agents.list[id=%s].%s blocks %s"
                        % (AGENT_ID, ".".join(walked), ".".join(path)))
                node = node[key]
            else:
                break


def capture_baseline(agent):
    """Baseline of the five fields: leaf value+presence AND parent presence."""
    baseline = {}
    for path in PATHS:
        parents = []
        node = agent
        for key in path[:-1]:
            if isinstance(node, dict) and key in node:
                parents.append(True)
                node = node[key]
            else:
                parents.append(False)
                node = None
                break
        # A missing parent means every deeper parent is also missing:
        # record False for the remaining levels instead of truncating.
        while len(parents) < len(path) - 1:
            parents.append(False)
        present = False
        value = None
        if node is not None and isinstance(node, dict) and path[-1] in node:
            present = True
            value = node[path[-1]]
        baseline[".".join(path)] = {
            "present": present, "value": value, "parents": parents}
    return baseline


def _validate_baseline(baseline):
    """Exact-schema + relationship validation; raises MigrationError on deviation."""
    if not isinstance(baseline, dict):
        raise MigrationError("baseline must be a dict")
    keys = set(baseline)
    expected = set(PATH_KEYS)
    missing = expected - keys
    if missing:
        raise MigrationError("baseline missing field records: %s" % sorted(missing))
    unknown = keys - expected
    if unknown:
        raise MigrationError("baseline has unknown field records")
    for key in PATH_KEYS:
        record = baseline[key]
        if not isinstance(record, dict):
            raise MigrationError("baseline record %s must be a dict" % key)
        extra = set(record) - {"present", "value", "parents"}
        if extra:
            raise MigrationError(
                "baseline record %s has unknown keys" % key)
        if "present" not in record:
            raise MigrationError("baseline record %s missing 'present'" % key)
        if not isinstance(record["present"], bool):
            raise MigrationError("baseline record %s: 'present' must be boolean" % key)
        if "value" not in record:
            raise MigrationError("baseline record %s missing 'value'" % key)
        expected_type = LEAF_TYPES[key]
        if record["present"] and not isinstance(record["value"], expected_type):
            raise MigrationError(
                "baseline record %s: value must be %s" % (key, expected_type.__name__))
        if not record["present"] and record["value"] is not None:
            raise MigrationError("baseline record %s: absent field must have null value" % key)
        # Installed-schema enum membership (message never echoes the value).
        if record["present"] and key in ENUMS and record["value"] not in ENUMS[key]:
            raise MigrationError(
                "baseline record %s: value is not an installed schema enum value (%s)"
                % (key, ", ".join(ENUMS[key])))
        parents = record.get("parents")
        depth = len(key.split(".")) - 1
        if not isinstance(parents, list) or len(parents) != depth \
                or not all(isinstance(p, bool) for p in parents):
            raise MigrationError(
                "baseline record %s: 'parents' must be %d booleans" % (key, depth))
        if record["present"] and not all(parents):
            raise MigrationError("baseline record %s: present field has absent parent" % key)
        # Within-record: a present parent after an absent one is impossible.
        seen_absent = False
        for flag in parents:
            if flag and seen_absent:
                raise MigrationError(
                    "baseline record %s: parent present after absent parent" % key)
            if not flag:
                seen_absent = True
    # Across records: shared parent prefixes must agree on presence.
    seen_prefixes = {}
    for key in PATH_KEYS:
        parts = key.split(".")
        parents = baseline[key]["parents"]
        for lvl in range(len(parts) - 1):
            prefix = ".".join(parts[: lvl + 1])
            flag = parents[lvl]
            if prefix in seen_prefixes and seen_prefixes[prefix] != flag:
                raise MigrationError(
                    "baseline records disagree on parent presence for %s" % prefix)
            seen_prefixes[prefix] = flag


def _apply_enable(agent):
    for path, value in ENABLED_VALUES.items():
        node = agent
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value


def enable(config, baseline=None):
    """Enable unsandboxed execution for the 'pixel' agent entry.

    baseline=None: capture a fresh baseline from the current config.
    baseline given (repeated enable, e.g. on an already-enabled config):
    validated in full and returned unchanged, preserving the original.

    Transactional: works on a deep copy; the caller's config is returned
    unmodified on any validation error. Returns (new_config, baseline).
    """
    _validate_root(config)
    working = copy.deepcopy(config)
    agent = _select_agent(working)
    _validate_shapes(agent)
    if baseline is None:
        baseline = capture_baseline(agent)
        _validate_baseline(baseline)  # a fresh baseline must itself be valid
    else:
        _validate_baseline(baseline)
    _apply_enable(agent)
    return working, baseline


def restore(config, baseline):
    """Restore the pixel entry's five fields from a separately-held baseline.

    Validates the root shape, full baseline schema and relationships, and all
    current config shapes BEFORE mutating (transactional deep-copy commit).
    Absent leaves are restored to absence; only parents that were absent
    originally (migration-created) are pruned. Preexisting empty objects are
    preserved. Unrelated fields and later edits are untouched.
    Returns (new_config, True).
    """
    _validate_root(config)
    working = copy.deepcopy(config)
    agent = _select_agent(working)
    _validate_baseline(baseline)
    _validate_shapes(agent)  # current malformed state blocks restore entirely
    for path in PATHS:
        record = baseline[".".join(path)]
        node = agent
        stack = []  # (parent, key) along the changed path
        for i, key in enumerate(path[:-1]):
            if key in node:
                stack.append((node, key))
                node = node[key]
            else:
                parent = node
                node = node.setdefault(key, {})
                stack.append((parent, key))
        if record["present"]:
            node[path[-1]] = record["value"]
        else:
            node.pop(path[-1], None)
            # Prune in reverse ONLY parents that were absent in the original
            # (per baseline 'parents') AND are now empty; stop at the first
            # preexisting parent so preexisting empty objects survive.
            for i in reversed(range(len(stack))):
                parent, key = stack[i]
                if record["parents"][i]:
                    break
                child = parent.get(key)
                if isinstance(child, dict) and not child:
                    parent.pop(key, None)
                else:
                    break
    return working, True
