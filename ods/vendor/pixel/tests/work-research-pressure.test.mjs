import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalizePublicUrl, normalizePublicQuery, ResearchBrokerError,
} from "../deploy/work-research-broker/broker.mjs";

const research = Object.freeze({
  allowedDomains: [],
  deniedDomains: ["blocked.example"],
});

test("public query DLP rejects secret, identity, address, path, encoding, and hidden-text variants", () => {
  const hostile = [
    "person@example.com public record", "SSN 123-45-6789 lookup", "call 212-555-0199 for context",
    "card 4111 1111 1111 1111 transaction", "API_KEY=shortsecret documentation",
    "sk-abcdefghijklmnopqrstuvwxyz123456", `AKIA${"ABCDEFGHIJKLMNOP"} usage`, `ghp_${"abcdefghijklmnopqrstuvwxyz123456"}`,
    "eyJabcdefghijklmno.abcdefghijklmnop.abcdefghijklmnop", `-----BEGIN ${"RSA PRIVATE KEY"}-----`,
    "C:\\Users\\Client\\secret.txt analysis", "/home/client/private.txt analysis", "file:///etc/passwd behavior",
    "\\\\server\\private\\roadmap.docx summary", "host 2001:db8::1 diagnostics", "host 192.0.2.10 diagnostics",
    "api%5fkey%3dsecret research", `normal​ hidden query`, `normal‮goal research`,
    `long ${"a".repeat(64)} token`, `encoded ${Buffer.from("private credential payload").toString("base64")} token`,
    "pеrson@еxamplе.com record", "contact pеrson@company.сom now",
    "verify id 123 45 6789 dataset", "record 123.45.6789 lookup",
    "lookup ONVS23DJOZSS2Q2BJZAVEWJNMFRGGZDFMYYDCMRTGQ2TMNZYHFAUEQ2EIVDA archive",
    "inspect C:/Users/jdoe/secret.env defaults", "review /opt/myapp/config/secrets.env now",
    "route through host 2130706433 benchmark",
    // Named secret env-var assignments whose secret word follows an underscore (so \btoken/\bsecret
    // miss them) must not egress to the public search provider. Sibling of the frontier DLP fix.
    "DEPLOY_TOKEN: hunterdeploy exfil", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEletmein now",
    "GITHUB_TOKEN=mytokenvalue lookup", "DATABASE_PASSWORD: correcthorse research",
    "SESSION_SECRET=abcdefghij docs", "ENCRYPTION_KEY: mykeyvalue analysis",
  ];
  for (const query of hostile) assert.throws(() => normalizePublicQuery(query), ResearchBrokerError, query);
  for (const query of [
    "C++ agent sandbox design", "OpenAI API authentication documentation", "IPv6 security best practices",
    "fifty percent failure rate research", "public zero trust agent architecture",
    // Benign confusable text (Cyrillic а in "agent") must NOT be over-rejected: its folded
    // skeleton forms no private-data pattern, so the query is returned unchanged.
    "аgent orchestration research",
    // Benign edges guarding the new PII/obfuscation rules against over-rejection.
    "dataset with 1000000000 rows performance", "how to store .env files securely",
    "survey 2019 2020 2021 adoption trends", "upgrade from 1.2.3 to 4.5.6 migration",
    // Value-less secret-name mentions, lowercase identifiers, and database key columns are not
    // over-rejected (guards the new named-secret-assignment rule against false positives).
    "how to rotate DEPLOY_TOKEN in CI safely", "SQL PRIMARY_KEY autoincrement syntax",
    "difference between SORT_KEY and PARTITION_KEY in dynamodb", "how does deploy_token work in yaml",
  ]) assert.equal(normalizePublicQuery(query), query);
});

test("public URL policy rejects non-public and credential-bearing URL forms", () => {
  const hostile = [
    "http://example.com/", "https://user:pass@example.com/", "https://localhost/", "https://service.internal/",
    "https://127.0.0.1/", "https://2130706433/", "https://[::1]/", "https://example.com:444/",
    "https://blocked.example/", "https://sub.blocked.example/", "https://example.com/?token=secret",
    "https://example.com/?access_token=secret", "https://example.com/?api%5fkey=secret",
  ];
  for (const url of hostile) assert.throws(() => canonicalizePublicUrl(url, research), ResearchBrokerError, url);
  assert.equal(canonicalizePublicUrl("https://example.com/#not-stored", research), "https://example.com/");
});

test("public URL canonicalization is deterministic and idempotent under parameter permutations", () => {
  let state = 0x5eed1234;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state;
  };
  for (let iteration = 0; iteration < 500; iteration += 1) {
    const entries = [
      ["z", String(random() % 100)], ["a", String(random() % 100)], ["b", String(random() % 100)],
      ["utm_source", "pressure"], ["ref", "tracker"],
    ];
    entries.sort(() => (random() & 1 ? 1 : -1));
    const input = `https://EXAMPLE.com/path?${new URLSearchParams(entries)}#fragment`;
    const canonical = canonicalizePublicUrl(input, research);
    assert.equal(canonicalizePublicUrl(canonical, research), canonical);
    assert.equal(new URL(canonical).hash, "");
    assert.equal(new URL(canonical).searchParams.has("utm_source"), false);
    assert.equal(new URL(canonical).searchParams.has("ref"), false);
  }
});
