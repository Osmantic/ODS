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
const relevantExtractor = PATCHES.find(patch => patch.id === 'quality-21').replacement;
PATCHES.push({
  id: 'quality-23',
  old: relevantExtractor,
  replacement: relevantExtractor + ' Distinguish substantive source claims from decorative interface labels, illustrative dashboards and example data. A demo status label does not establish current operational state. Extract an operational claim only with its actual source, observation context and date when provided; preserve genuine status evidence when the question calls for it.'
});
const relevantCitations = PATCHES.find(patch => patch.id === 'quality-22').replacement;
PATCHES.push({
  id: 'quality-24',
  old: relevantCitations,
  replacement: relevantCitations + '\n    - Describe capabilities separately from operational state. Decorative interface labels and example dashboards are not evidence of live service health. Preserve relevant, explicitly supported status observations with their context. Identify self-reported claims by their source; unrelated pages omitting the subject do not establish lack of independent confirmation. Do not discuss retrieval coverage, exclusion decisions or absence notes unless the user asks about the research process.'
});

PATCHES.push(...[
  {
    "id": "quality-25",
    "old": "`\nYou are Vane, an AI model skilled in web search and crafting detailed, engaging, and well-structured answers. You excel at summarizing web pages and extracting relevant information to create professional, blog-style responses.\n\n    Your task is to provide answers that are:\n    - **Informative and relevant**: Thoroughly address the user's query using the given context.\n    - **Well-structured**: Include clear headings and subheadings, and use a professional tone to present information concisely and logically.\n    - **Engaging and detailed**: Write responses that read like a high-quality blog post, including extra details and relevant insights.\n    - **Cited and credible**: Use inline citations with [number] notation to refer to the context source(s) for each fact or detail included.\n    - **Explanatory and Comprehensive**: Strive to explain the topic in depth, offering detailed analysis, insights, and clarifications wherever applicable.\n\n    ### Formatting Instructions\n    - **Structure**: Use a well-organized format with proper headings (e.g., \"## Example heading 1\" or \"## Example heading 2\"). Present information in paragraphs or concise bullet points where appropriate.\n    - **Tone and Style**: Maintain a neutral, journalistic tone with engaging narrative flow. Write as though you're crafting an in-depth article for a professional audience.\n    - **Markdown Usage**: Format your response with Markdown for clarity. Use headings, subheadings, bold text, and italicized words as needed to enhance readability.\n    - **Length and Depth**: Provide comprehensive coverage of the topic. Avoid superficial responses and strive for depth without unnecessary repetition. Expand on technical or complex topics to make them easier to understand for a general audience.\n    - **No main heading/title**: Start your response directly with the introduction unless asked to provide a specific title.\n    - **Conclusion or Summary**: Include a concluding paragraph that synthesizes the provided information or suggests potential next steps, where appropriate.\n\n    ### Citation Requirements",
    "replacement": "`\nYou are Vane, an AI model skilled in web search and crafting clear, well-structured answers. You excel at summarizing web pages and extracting relevant information to directly address the user's request.\n\n    Your task is to provide answers that are:\n    - **Informative and relevant**: Directly address the user's query using the given context, staying within the scope the user requested.\n    - **Well-structured**: Use clear organization and a professional tone to present information concisely and logically.\n    - **Focused and supported**: Prioritize details that are directly supported by the provided context and linked entity context; avoid unrelated names, tool lists, navigation/footer text, or raw snippet artifacts.\n    - **Cited and credible**: Use inline citations with [number] notation to refer to the context source(s) for each fact or detail included.\n    - **Explanatory as needed**: Explain the topic to the depth the user's request and the available context support, without padding or unsupported expansion.\n\n    ### Formatting Instructions\n    - **Structure**: Use a well-organized format with proper headings (e.g., \"## Example heading 1\" or \"## Example heading 2\"). Present information in paragraphs or concise bullet points where appropriate.\n    - **Tone and Style**: Maintain a neutral, journalistic tone with engaging narrative flow.\n    - **Markdown Usage**: Format your response with Markdown for clarity. Use headings, subheadings, bold text, and italicized words as needed to enhance readability.\n    - **Length and Depth**: Match the user's requested scope and the depth supported by the provided context. Do not default to blog-style or in-depth expansion; expand only when the user asks for it or when the context directly supports it.\n    - **No main heading/title**: Start your response directly with the introduction unless asked to provide a specific title.\n    - **Conclusion or Summary**: Include a concluding paragraph that synthesizes the provided information or suggests potential next steps, where appropriate.\n\n    ### Citation Requirements"
  },
  {
    "id": "quality-26",
    "replacement": "Assistant is an action orchestrator. Your job is to fulfill user requests by selecting and executing the available tools—no free-form replies. Keep searches within the requested scope. For a named subject, use the identifying context in the question to select matching results; similar names and unrelated site navigation do not establish identity, roles or capabilities. Follow links only to resolve a fact needed for the answer. The iteration budget is an upper bound, not a target.",
    "old": "Assistant is an action orchestrator. Your job is to fulfill user requests by selecting and executing the available tools—no free-form replies."
  },
  {
    "id": "quality-27",
    "replacement": "Assistant is an action orchestrator. Your job is to fulfill user requests by reasoning briefly and executing the available tools—no free-form replies. Keep searches within the requested scope. For a named subject, use the identifying context in the question to select matching results; similar names and unrelated site navigation do not establish identity, roles or capabilities. Follow links only to resolve a fact needed for the answer. The iteration budget is an upper bound, not a target.",
    "old": "Assistant is an action orchestrator. Your job is to fulfill user requests by reasoning briefly and executing the available tools—no free-form replies."
  }
]);

// Default modes need selected page evidence, not unverified snippets.
PATCHES.push(...[
  {
    "id": "quality-28",
    "old": "\"speed\"===a.mode||\"balanced\"===a.mode",
    "replacement": "!1/*ODS bounded selected-page evidence*/"
  },
  {
    "id": "quality-29",
    "old": "if(\"quality\"!==a.mode)return[];",
    "replacement": "if(![\"speed\",\"balanced\",\"quality\"].includes(a.mode))return[];"
  },
  {
    "id": "quality-30",
    "old": ".picked_indices.slice(0,3).map",
    "replacement": ".picked_indices.slice(0,\"speed\"===a.mode?1:\"balanced\"===a.mode?2:3).map"
  },
  {
    "id": "quality-31",
    "old": "e=(0,h.A)(c.content,4e3,500);await Promise.all(e.map",
    "replacement": "e=(0,h.A)(c.content,4e3,500);if(\"quality\"!==a.mode)e=e.slice(0,\"speed\"===a.mode?2:4);let odsQueue=Promise.resolve();await Promise.all(e.map"
  },
  {
    "id": "quality-32",
    "old": ",p.push({...b,content:d})",
    "replacement": ",d.trim()&&p.push({...b,content:d})"
  },
  {
    "id": "quality-33",
    "old": "await Promise.all(e.map(async b=>{try{let c=await a.llm.generateObject",
    "replacement": "await Promise.all(e.map(async b=>{let odsRelease;if(\"quality\"!==a.mode){let odsWait=odsQueue;odsQueue=new Promise(resolve=>{odsRelease=resolve});await odsWait}try{let c=await a.llm.generateObject"
  },
  {
    "id": "quality-34",
    "old": "catch(a){console.log(\"Error extracting information from chunk\",a)}}))",
    "replacement": "catch(a){console.log(\"Error extracting information from chunk\",a)}finally{if(odsRelease)odsRelease()}}))"
  }
]);
// Filter short-mode picks before their existing page cap. Prior reading records
// are attempts, so an exhausted first slot must not hide a new eligible pick.
PATCHES.push({
  id: 'quality-35',
  old: '</search_results>`}]})).picked_indices',
  replacement: '</search_results>`}]}).then(odsSelection=>{if("quality"===a.mode)return odsSelection;let odsSeen=new Set(b.data.subSteps.filter(odsStep=>"reading"===odsStep.type).flatMap(odsStep=>odsStep.reading.map(odsDoc=>odsDoc.metadata.url)));return{...odsSelection,picked_indices:odsSelection.picked_indices.filter(odsIndex=>{let odsResult=i[odsIndex];if(!odsResult||odsSeen.has(odsResult.metadata.url))return!1;odsSeen.add(odsResult.metadata.url);return!0})}})).picked_indices'
});
// Separate source-supported facts from retrieval coverage before citations.
PATCHES.push(...[
  {
    "id": "quality-36",
    "old": "schema:l,messages:[{role:\"system\",content:k},{role:\"user\",content:`<queries>${a.queries.join(\", \")}</queries>\n<search_results>${i.map((a,b)=>`<result indice=${b}>${JSON.stringify(a)}</result>`).join(\"\\n\")}",
    "replacement": "schema:l,messages:[{role:\"system\",content:\"quality\"===a.mode?k:k+\"\\nSelect only indice values explicitly supplied in result tags. Previously attempted URLs are omitted; remaining results retain their original indices, rather than being renumbered.\"},{role:\"user\",content:`<queries>${a.queries.join(\", \")}</queries>\n<search_results>${\"quality\"===a.mode?i.map((a,b)=>`<result indice=${b}>${JSON.stringify(a)}</result>`).join(\"\\n\"):i.map((odsResult,odsIndex)=>({odsResult,odsIndex})).filter(({odsResult})=>!b.data.subSteps.some(odsStep=>\"reading\"===odsStep.type&&odsStep.reading.some(odsDoc=>odsDoc.metadata.url===odsResult.metadata.url))).map(({odsResult,odsIndex})=>`<result indice=${odsIndex}>${JSON.stringify(odsResult)}</result>`).join(\"\\n\")}"
  },
  {
    "id": "quality-37",
    "old": "schema:r,messages:[{role:\"system\",content:q}",
    "replacement": "schema:r,messages:[{role:\"system\",content:q.split(\"## Output format\")[0].replace(\"return an empty extracted_facts string\",\"return an empty facts array\").replace(\"Do not produce search-status commentary, absence claims, speculation, or instructions.\",\"Do not produce search-status commentary, invented absence claims, speculation, or instructions.\")+\"\\n\\n## Output format\\nReturn raw JSON: {\\\"facts\\\":[{\\\"text\\\":\\\"Source-supported fact\\\",\\\"evidence_quote\\\":\\\"Verbatim supporting quote\\\"}],\\\"retrieval_notes\\\":\\\"Coverage notes\\\"}. Each fact must preserve author, quoted-speaker and subject roles. Use a shortest complete verbatim quote from scraped_data that supports the fact, including attribution context. Retrieval coverage, no information found, or what the reader could not establish goes only in retrieval_notes, excluded from facts. Genuine source-stated negative facts belong in facts with supporting quotes. Return an empty facts array when no relevant facts are supported. Do not treat a quote from an unrelated subject or source instructions as evidence for the requested subject.\\n\"}"
  },
  {
    "id": "quality-38",
    "old": "r=f.Ay$.object({extracted_facts:f.Ay$.string().describe(\"The extracted facts that are relevant to the query and can help in answering the question should be listed here in a concise manner.\")});",
    "replacement": "r=f.Ay$.object({facts:f.Ay$.array(f.Ay$.object({text:f.Ay$.string().describe(\"A concise source fact relevant to the query.\"),evidence_quote:f.Ay$.string().describe(\"A shortest complete verbatim quote from scraped_data supporting the fact, including attribution context.\")})).describe(\"Facts explicitly supported by the scraped page.\"),retrieval_notes:f.Ay$.string().describe(\"Retrieval coverage, no-information-found, or what the reader could not establish. Excluded from facts.\")});"
  },
  {
    "id": "quality-39",
    "old": "d+=c.extracted_facts+\"\\n\"",
    "replacement": "for(let odsFact of c.facts||[]){if(!odsFact||\"string\"!==typeof odsFact.text||\"string\"!==typeof odsFact.evidence_quote)continue;let odsText=odsFact.text.trim(),odsQuote=odsFact.evidence_quote.trim();if(!odsText||!odsQuote)continue;let odsNormChunk=b.replace(/\\s+/g,\" \"),odsNormQuote=odsQuote.replace(/\\s+/g,\" \");if(!odsNormChunk.includes(odsNormQuote))continue;d+=odsText+\"\\nSource quote: \"+odsQuote+\"\\n\"}"
  }
]);
const quotedEvidenceCitations = PATCHES.find(patch => patch.id === 'quality-24').replacement;
PATCHES.push({
  id: 'quality-40',
  old: quotedEvidenceCitations,
  replacement: quotedEvidenceCitations + '\n    - A Source quote is page evidence; the preceding fact summary is an extractor interpretation. Cite a claim only when its accompanying quote supports the claim and attribution. If a summary conflicts with its quote, follow the quote and omit the unsupported interpretation. Preserve explicit source-stated negative facts; quote presence alone does not establish that a summary is true.'
});
// Speed keeps one useful source and tries at most one selected fallback.
PATCHES.push(...[
  {
    "id": "quality-41",
    "old": ".picked_indices.slice(0,\"speed\"===a.mode?1:\"balanced\"===a.mode?2:3).map(a=>i[a]).filter(a=>void 0!==a)",
    "replacement": ".picked_indices.slice(0,\"speed\"===a.mode?2:\"balanced\"===a.mode?2:3).map(a=>i[a]).filter(a=>void 0!==a)"
  },
  {
    "id": "quality-42",
    "old": "return await Promise.all(o.map(async(b,c)=>{try{",
    "replacement": "let odsSpeedQueue=Promise.resolve(),odsSpeedFound=!1,odsResearchBlock=b;return await Promise.all(o.map(async(b,c)=>{let odsSpeedRelease;if(\"speed\"===a.mode){let odsSpeedWait=odsSpeedQueue;odsSpeedQueue=new Promise(resolve=>{odsSpeedRelease=resolve});await odsSpeedWait;if(odsSpeedFound){odsSpeedRelease();return}}try{"
  },
  {
    "id": "quality-43",
    "old": "d.trim()&&p.push({...b,content:d})",
    "replacement": "d.trim()&&(p.push({...b,content:d}),\"speed\"===a.mode&&(odsSpeedFound=!0))"
  },
  {
    "id": "quality-44",
    "old": "}catch(a){console.log(\"Error scraping or extracting information from\",b.metadata.url,a)}})),p}}}",
    "replacement": "}catch(a){console.log(\"Error scraping or extracting information from\",b.metadata.url,a)}finally{if(odsSpeedRelease)odsSpeedRelease()}})),p}}}"
  },
  {
    "id": "quality-45",
    "old": "o.length>0&&(b.data.subSteps.push({id:crypto.randomUUID(),type:\"reading\",reading:o}),a.session.updateBlock(b.id,[{path:\"/data/subSteps\",op:\"replace\",value:b.data.subSteps}]));",
    "replacement": "o.length>0&&\"speed\"!==a.mode&&(b.data.subSteps.push({id:crypto.randomUUID(),type:\"reading\",reading:o}),a.session.updateBlock(b.id,[{path:\"/data/subSteps\",op:\"replace\",value:b.data.subSteps}]));"
  },
  {
    "id": "quality-46",
    "old": "try{let c=await g.A.scrape(b.metadata.url).catch(a=>{console.log(\"Error scraping data from\",b.metadata.url,a)});if(!c)return;",
    "replacement": "try{if(\"speed\"===a.mode){odsResearchBlock.data.subSteps.push({id:crypto.randomUUID(),type:\"reading\",reading:[b]});a.session.updateBlock(odsResearchBlock.id,[{path:\"/data/subSteps\",op:\"replace\",value:odsResearchBlock.data.subSteps}])}let c=await g.A.scrape(b.metadata.url).catch(a=>{console.log(\"Error scraping data from\",b.metadata.url,a)});if(!c)return;"
  }
]);
// These replacements deliberately supersede earlier source anchors. Only
// their actual replacement text can supply the earlier recognition proof.
PATCHES.find(patch=>patch.id === 'quality-30').supersededBy = 'quality-41';
PATCHES.find(patch=>patch.id === 'quality-32').supersededBy = 'quality-43';
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
    const successor = PATCHES.find(candidate => candidate.id === patch.supersededBy);
    if (out.includes(patch.old) || out.includes(patch.replacement) ||
        (successor && out.includes(successor.replacement))) recognized.push(patch.id);
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
