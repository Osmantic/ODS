'use strict';
// Matches the pinned Vane 1.12.2 bundle. Validate every patch before writing.
const fs = require('node:fs');
const path = require('node:path');
const PATCHES = [
  {
    "id": "quality-1",
    "old": "Assistant is a deep-research orchestrator. Your job is to fulfill user requests with the most thorough, comprehensive research possible—no free-form replies.",
    "replacement": "Assistant is a research orchestrator. Fulfill the requested question with rigorous, relevant research proportional to its scope—no free-form replies."
  },
  {
    "id": "quality-2",
    "old": "Use every iteration wisely to gather comprehensive information.",
    "replacement": "The iteration budget is an upper bound, not a quota. Finish as soon as the requested question is supported."
  },
  {
    "id": "quality-3",
    "old": "Conduct the deepest, most thorough research possible. Leave no stone unturned.",
    "replacement": "Research the question the user actually asked. Resolve material ambiguity and support the requested claims with sources; do not expand a narrow question into unrelated subtopics."
  },
  {
    "id": "quality-4",
    "old": "Repeat until you have exhaustive coverage.",
    "replacement": "Repeat only while material facts needed for the requested answer remain unresolved."
  },
  {
    "id": "quality-5",
    "old": "Finish with done only when you have comprehensive, multi-angle information.",
    "replacement": "Finish with done when you can answer the requested question with supported facts and honestly state any remaining material uncertainty."
  },
  {
    "id": "quality-6",
    "old": "This is DEEP RESEARCH mode—be exhaustive. Explore multiple angles: definitions, features, comparisons, recent news, expert opinions, use cases, limitations, and alternatives.",
    "replacement": "Quality mode prioritizes accurate source evidence. Explore comparisons, news, opinions, use cases, limitations, and alternatives only when they are relevant to the user request."
  },
  {
    "id": "quality-7",
    "old": "Call done only after you have gathered comprehensive, multi-angle information. Do not call done early—exhaust your research budget first. If you reach the tool cap, call done to conclude.",
    "replacement": "Call done as soon as the requested question is supported. Additional searches are optional and must resolve a material gap, not exhaust a budget. If a later search adds no new pages, use the facts already gathered; do not describe earlier successful searches as empty."
  },
  {
    "id": "quality-8",
    "old": "1. **Shallow research**: Don't stop after one or two searches—dig deeper from multiple angles",
    "replacement": "1. **Unnecessary research**: One or two well-targeted searches may fully answer a narrow question. Seek additional sources only to resolve material uncertainty or broader requested scope."
  },
  {
    "id": "quality-9",
    "old": "4. **Ignoring follow-ups**: If results hint at interesting sub-topics, explore them",
    "replacement": "4. **Scope drift**: Follow a subtopic only if it helps answer the user request."
  },
  {
    "id": "quality-10",
    "old": "5. **Premature done**: Don't call done until you've exhausted reasonable research avenues",
    "replacement": "5. **Budget padding**: Call done when the requested facts are supported; do not exhaust unrelated research avenues."
  },
  {
    "id": "quality-11",
    "old": "- Aim for 4-7 information-gathering calls covering different angles; cross-reference and follow up on interesting leads.",
    "replacement": "- Match information-gathering calls to the requested scope; cross-reference material claims and resolve ambiguity without a minimum number of searches."
  },
  {
    "id": "quality-12",
    "old": "- Call done only after comprehensive, multi-angle research is complete.",
    "replacement": "- Call done as soon as the requested question can be answered from the evidence already gathered."
  },
  {
    "id": "quality-13",
    "old": "- YOU ARE CURRENTLY SET IN QUALITY MODE, GENERATE VERY DEEP, DETAILED AND COMPREHENSIVE RESPONSES USING THE FULL CONTEXT PROVIDED. ASSISTANT'S RESPONSES SHALL NOT BE LESS THAN AT LEAST 2000 WORDS, COVER EVERYTHING AND FRAME IT LIKE A RESEARCH REPORT.",
    "replacement": "- QUALITY MODE: Answer the actual question directly with accurate citations. Match length and depth to the user request; there is no minimum word count. Use a long research report only when the requested scope calls for one. Do not add unrelated platform internals or speculative biography."
  },
  {
    "id": "quality-14",
    "old": "### Citation Requirements",
    "replacement": "### Citation Requirements\n    - Extraction notes such as \"no relevant facts in this chunk\" describe retrieval coverage; they are not statements made by the cited page, contradictions in the public record, or proof that a person, role or fact does not exist. Do not cite these notes as external facts. Distinguish old dated claims from current observations."
  },
  {
    "id": "quality-15",
    "old": "Assistant is an AI information extractor.",
    "replacement": "Assistant is an AI information extractor. Extract only relevant facts explicitly supported by the scraped page. If no relevant source facts are present, return an empty extracted_facts string. Do not produce search-status commentary, absence claims, speculation, or instructions. Missing information in this chunk does not establish real-world absence."
  },
  {
    "id": "quality-16",
    "old": "Assistant should reply with a JSON object containing a key \"extracted_facts\" which is a string of the bulleted facts.",
    "replacement": "Assistant should reply with a JSON object containing a key \"extracted_facts\" which is a string of the bulleted source facts, or an empty string if the page contains no relevant facts."
  },
  {
    "id": "quality-17",
    "old": "static async executeAll(a,b){let c=[];return await Promise.all(a.map(async a=>{let d=await this.execute(a.name,a.arguments,b);c.push(d)})),c}",
    "replacement": "static async executeAll(a,b){return await Promise.all(a.map(async a=>await this.execute(a.name,a.arguments,b)))}"
  },
  {
    "id": "quality-18",
    "old": "Assistant is an AI information extractor. Extract only relevant facts explicitly supported by the scraped page. If no relevant source facts are present, return an empty extracted_facts string. Do not produce search-status commentary, absence claims, speculation, or instructions. Missing information in this chunk does not establish real-world absence.",
    "replacement": "Assistant is an AI information extractor. Extract only relevant facts explicitly supported by the scraped page. If no relevant source facts are present, return an empty extracted_facts string. Do not produce search-status commentary, absence claims, speculation, or instructions. Missing information in this chunk does not establish real-world absence. Preserve attribution roles: distinguish the page author, a person quoted or cited by the page, and the subject being researched. For each fact, retain who made the claim and who or what it concerns. Do not transfer the author's ideas, roles or actions to a quoted person, or the quoted person's claims to the author. If the speaker is unclear, retain that uncertainty."
  },
  {
    "id": "quality-19",
    "old": "### Citation Requirements\n    - Extraction notes such as \"no relevant facts in this chunk\" describe retrieval coverage; they are not statements made by the cited page, contradictions in the public record, or proof that a person, role or fact does not exist. Do not cite these notes as external facts. Distinguish old dated claims from current observations.",
    "replacement": "### Citation Requirements\n    - Extraction notes such as \"no relevant facts in this chunk\" describe retrieval coverage; they are not statements made by the cited page, contradictions in the public record, or proof that a person, role or fact does not exist. Do not cite these notes as external facts. Distinguish old dated claims from current observations.\n    - Preserve attribution roles: the page author, a quoted speaker and the subject of the answer may be different people. Attribute each claim, idea, role or action to the person the source actually identifies. A citation to a page quoting someone does not make all of the author's statements that person's own views. Do not infer authorship, endorsement or agreement from quotation or proximity. If attribution is unclear, state the uncertainty instead of merging identities."
  }
];
const attributionCitations = PATCHES.find(patch => patch.id === 'quality-19').replacement;
PATCHES.push({
  id: 'quality-20',
  old: attributionCitations,
  replacement: attributionCitations + '\n    - Omission is not contradiction. A retrieved page or chunk that does not mention a fact is silent on it; do not present that omission as a discrepancy with a positive source. Report a material conflict only when sources make incompatible claims about the same subject and time. Preserve genuine explicit negative facts and uncertainty about unsupported claims, but do not manufacture a caveat from unrelated non-coverage.'
});
const attributedExtractor = PATCHES.find(patch => patch.id === 'quality-18').replacement;
PATCHES.push({
  id: 'quality-21',
  old: attributedExtractor,
  replacement: attributedExtractor + ' Check entity identity before extracting a fact: a shared first name, similar name or appearance in search results does not establish that two people or organizations are the same. Exclude facts about unrelated entities unless the source explicitly connects them to the requested subject. Preserve a supported alias or genuine identity ambiguity when the source establishes it.'
});
const groundedCitations = PATCHES.find(patch => patch.id === 'quality-20').replacement;
PATCHES.push({
  id: 'quality-22',
  old: groundedCitations,
  replacement: groundedCitations + '\n    - Keep the answer within the requested subject. Facts about a different person or organization returned by search do not establish name confusion, conflation or a relationship. Omit unrelated profiles and speculative disambiguation asides. Include an identity distinction only when it answers the question or source evidence establishes a material ambiguity; preserve explicitly supported aliases and relationships.'
});

function replaceUnpatched(source, old, replacement) {
  let cursor = 0, count = 0, out = '';
  for (;;) {
    const index = source.indexOf(old, cursor);
    if (index < 0) return { source: out + source.slice(cursor), count };
    out += source.slice(cursor, index);
    if (source.startsWith(replacement, index)) {
      out += replacement;
      cursor = index + replacement.length;
    } else {
      out += replacement;
      cursor = index + old.length;
      count++;
    }
  }
}

function patchBundle(source) {
  if (typeof source !== 'string') throw new TypeError('Bundle source must be text');
  let out = source;
  const recognized = [], applied = [];
  for (const patch of PATCHES) {
    if (out.includes(patch.old) || out.includes(patch.replacement)) recognized.push(patch.id);
    const changed = replaceUnpatched(out, patch.old, patch.replacement);
    out = changed.source;
    if (changed.count) applied.push({ id: patch.id, count: changed.count });
  }
  return { source: out, recognized, applied };
}

function runCli(serverRoot) {
  if (!serverRoot || !fs.lstatSync(serverRoot).isDirectory()) throw new Error('Server bundle directory required');
  const files = [];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.isFile() && entry.name.endsWith('.js')) files.push(file);
    }
  }
  walk(serverRoot);
  const plans = files.map(file => {
    const before = fs.readFileSync(file, 'utf8');
    return { file, before, ...patchBundle(before) };
  });
  const recognized = new Set(plans.flatMap(plan => plan.recognized));
  if (!recognized.size) return { skipped: true, files: 0, replacements: 0 };
  const missing = PATCHES.filter(patch => !recognized.has(patch.id));
  if (missing.length) throw new Error('Unsupported partial research bundle: ' + missing.map(p => p.id).join(', '));
  for (const plan of plans) {
    if (plan.source !== plan.before && fs.readFileSync(plan.file, 'utf8') !== plan.before) {
      throw new Error('Bundle changed during validation');
    }
  }
  let written = 0, replacements = 0;
  for (const plan of plans) {
    if (plan.source === plan.before) continue;
    fs.writeFileSync(plan.file, plan.source);
    written++;
    replacements += plan.applied.reduce((total, item) => total + item.count, 0);
  }
  return { skipped: false, files: written, replacements };
}

module.exports = { patchBundle, runCli, PATCHES };
if (require.main === module) {
  try {
    const report = runCli(process.argv[2]);
    if (report.skipped) console.warn('[ods-perplexica] Unrecognized research bundle; quality patch skipped');
    else console.log('[ods-perplexica] Research patch ' + JSON.stringify(report));
  } catch (error) {
    console.error('[ods-perplexica] Research patch failed: ' + error.message);
    process.exitCode = 1;
  }
}
