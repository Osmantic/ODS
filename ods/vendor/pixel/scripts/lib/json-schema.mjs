function stable(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(",")}}`;
}

function equal(left, right) {
  return stable(left) === stable(right);
}

function typeMatches(value, type) {
  if (Array.isArray(type)) return type.some((candidate) => typeMatches(value, candidate));
  if (type === "array") return Array.isArray(value);
  if (type === "integer") return Number.isInteger(value);
  if (type === "null") return value === null;
  if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  return typeof value === type;
}

function resolveReference(root, reference) {
  if (!reference.startsWith("#/")) throw new Error(`Only local JSON Schema references are supported: ${reference}`);
  return reference.slice(2).split("/").reduce((value, token) => value[token.replaceAll("~1", "/").replaceAll("~0", "~")], root);
}

function validCalendarDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const observed = new Date(0);
  observed.setUTCHours(0, 0, 0, 0);
  observed.setUTCFullYear(year, month - 1, day);
  return observed.getUTCFullYear() === year && observed.getUTCMonth() === month - 1 && observed.getUTCDate() === day;
}

function validDateTime(value) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/.exec(value);
  return Boolean(match) && validCalendarDate(match[1]) && Number(match[2]) <= 23 && Number(match[3]) <= 59 && Number(match[4]) <= 59 && !Number.isNaN(Date.parse(value));
}

export function validateJsonSchema(value, schema) {
  const errors = [];
  function visit(current, rule, path, findings = errors) {
    if (rule === true) return;
    if (rule === false) {
      findings.push(`${path}: value is forbidden by schema`);
      return;
    }
    if (!rule || typeof rule !== "object" || Array.isArray(rule)) {
      findings.push(`${path}: schema rule is invalid`);
      return;
    }
    if (rule.$ref) {
      const referenced = resolveReference(schema, rule.$ref);
      if (!referenced) findings.push(`${path}: unresolved schema reference ${rule.$ref}`);
      else visit(current, referenced, path, findings);
      return;
    }
    const matches = (candidate) => {
      const probe = [];
      visit(current, candidate, path, probe);
      return probe.length === 0;
    };
    for (const child of rule.allOf ?? []) visit(current, child, path, findings);
    if (rule.anyOf && !rule.anyOf.some(matches)) findings.push(`${path}: value matches no allowed schema`);
    if (rule.oneOf && rule.oneOf.filter(matches).length !== 1) findings.push(`${path}: value must match exactly one allowed schema`);
    if (rule.not && matches(rule.not)) findings.push(`${path}: value matches a forbidden schema`);
    if (rule.if) {
      const branch = matches(rule.if) ? rule.then : rule.else;
      if (branch) visit(current, branch, path, findings);
    }
    if (rule.type && !typeMatches(current, rule.type)) {
      findings.push(`${path}: expected ${Array.isArray(rule.type) ? rule.type.join(" or ") : rule.type}`);
      return;
    }
    if (Object.hasOwn(rule, "const") && !equal(current, rule.const)) findings.push(`${path}: value differs from schema constant`);
    if (rule.enum && !rule.enum.some((candidate) => equal(current, candidate))) findings.push(`${path}: value is outside the schema enum`);
    if (typeof current === "string") {
      if (rule.minLength !== undefined && current.length < rule.minLength) findings.push(`${path}: string is too short`);
      if (rule.maxLength !== undefined && current.length > rule.maxLength) findings.push(`${path}: string is too long`);
      if (rule.pattern !== undefined && !new RegExp(rule.pattern).test(current)) findings.push(`${path}: string does not match ${rule.pattern}`);
      if (rule.format === "date" && !validCalendarDate(current)) findings.push(`${path}: invalid date`);
      if (rule.format === "date-time" && !validDateTime(current)) findings.push(`${path}: invalid date-time`);
    }
    if (typeof current === "number") {
      if (rule.minimum !== undefined && current < rule.minimum) findings.push(`${path}: number is below minimum`);
      if (rule.maximum !== undefined && current > rule.maximum) findings.push(`${path}: number exceeds maximum`);
    }
    if (Array.isArray(current)) {
      if (rule.minItems !== undefined && current.length < rule.minItems) findings.push(`${path}: array has too few items`);
      if (rule.maxItems !== undefined && current.length > rule.maxItems) findings.push(`${path}: array has too many items`);
      if (rule.uniqueItems && new Set(current.map(stable)).size !== current.length) findings.push(`${path}: array items are not unique`);
      if (rule.contains && !current.some((item, index) => {
        const probe = [];
        visit(item, rule.contains, `${path}[${index}]`, probe);
        return probe.length === 0;
      })) findings.push(`${path}: array does not contain a required matching item`);
      if (rule.items) current.forEach((item, index) => visit(item, rule.items, `${path}[${index}]`, findings));
    }
    if (current !== null && typeof current === "object" && !Array.isArray(current)) {
      const keys = Object.keys(current);
      if (rule.minProperties !== undefined && keys.length < rule.minProperties) findings.push(`${path}: object has too few properties`);
      if (rule.maxProperties !== undefined && keys.length > rule.maxProperties) findings.push(`${path}: object has too many properties`);
      for (const required of rule.required ?? []) if (!Object.hasOwn(current, required)) findings.push(`${path}: missing required property ${required}`);
      if (rule.propertyNames) for (const key of keys) visit(key, rule.propertyNames, `${path} property ${JSON.stringify(key)}`, findings);
      for (const [key, child] of Object.entries(current)) {
        // A "__proto__" own-key (which JSON.parse produces as an own enumerable property)
        // is never a legitimate schema field and is a prototype-pollution vector; reject it
        // regardless of schema. It must also not be looked up via `rule.properties[key]`,
        // where it would resolve through the prototype chain to Object.prototype (truthy)
        // and silently satisfy `additionalProperties: false` -- a fail-open control bypass.
        if (key === "__proto__") { findings.push(`${path}: unsafe property name ${key}`); continue; }
        if (rule.properties && Object.hasOwn(rule.properties, key)) visit(child, rule.properties[key], `${path}.${key}`, findings);
        else if (rule.additionalProperties === false) findings.push(`${path}: unexpected property ${key}`);
        else if (rule.additionalProperties && typeof rule.additionalProperties === "object") visit(child, rule.additionalProperties, `${path}.${key}`, findings);
      }
    }
  }
  visit(value, schema, "$");
  return errors;
}

export function assertJsonSchema(value, schema, label) {
  const errors = validateJsonSchema(value, schema);
  if (errors.length) throw new Error(`${label} failed schema validation:\n- ${errors.join("\n- ")}`);
}
