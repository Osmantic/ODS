// Chooses one stable locator for the element a show/hide inspection must
// assert on both sides of its click. It only writes the corrective steps of an
// INCOMPLETE inspection result; it never verifies anything.
//
// Tower2 round 102 asserted ".sold-out-card.hidden" before the click and
// ".sold-out-card:not(.hidden)" after it. Each locator includes the state it
// checks, so the two may match different elements. Laptop round 100 asserted
// only ".event-card.sold-out.revealed" after the click. Removing the state
// qualifiers from each locator's subject gives one locator for both sides. The
// published outline (requested-literals.mjs publishedElementOutline) must then
// show it names exactly one element, which contains the heading the owner
// named; that element's id is preferred.
import { isDeepStrictEqual } from 'node:util';

// Class names that describe a visibility or disclosure state. Classes the
// published scripts add, remove or toggle are added to these.
export const STATE_CLASS_WORDS = Object.freeze(['hidden', 'hide', 'is-hidden', 'visible', 'is-visible', 'show', 'shown',
  'showing', 'is-shown', 'open', 'opened', 'is-open', 'closed', 'is-closed', 'active', 'is-active', 'revealed', 'reveal',
  'is-revealed', 'expanded', 'is-expanded', 'collapsed', 'is-collapsed', 'd-none', 'invisible', 'displayed']);
const STATE_ATTRIBUTE = /^(?:hidden|open|inert|aria-(?:hidden|expanded)|data-(?:state|visible|hidden|open|shown|expanded|collapsed|active))$/i;
const IDENTIFIER = String.raw`-?[A-Za-z_][\w-]*`;
const TAG = /^(?:\*|[A-Za-z][\w-]*)/;
const ID = new RegExp(`^#(${IDENTIFIER})`);
const CLASS = new RegExp(`^\\.(${IDENTIFIER})`);
const ATTRIBUTE = /^\[\s*([A-Za-z_][\w-]*)\s*(?:[~|^$*]?=\s*(?:"[^"]*"|'[^']*'|[\w-]+)\s*)?\]/;
const NOT = /^:not\(\s*([^()]*?)\s*\)/;
const PSEUDO = /^::?[A-Za-z][\w-]*(?:\([^()]*\))?/;
const SAFE_ID = /^[A-Za-z_][\w-]*$/;

function simplePart(text, first) {
  let match;
  if (first && (match = TAG.exec(text))) return {kind: 'tag', value: match[0].toLowerCase(), raw: match[0]};
  if ((match = ID.exec(text))) return {kind: 'id', value: match[1], raw: match[0]};
  if ((match = CLASS.exec(text))) return {kind: 'class', value: match[1], raw: match[0]};
  if ((match = ATTRIBUTE.exec(text))) return {kind: 'attribute', value: match[1].toLowerCase(), raw: match[0]};
  return undefined;
}

function nextPart(text, first) {
  const simple = simplePart(text, first);
  if (simple) return simple;
  let match;
  if ((match = NOT.exec(text))) {
    const inner = simplePart(match[1], true);
    return inner && inner.raw === match[1] ? {kind: 'not', inner, raw: match[0]} : undefined;
  }
  if ((match = PSEUDO.exec(text))) return {kind: 'pseudo', raw: match[0]};
  return undefined;
}

// Compounds joined by combinators; no selector lists or unsupported syntax.
export function parseSelector(selector) {
  if (typeof selector !== 'string' || selector.length > 256) return undefined;
  let rest = selector.trim();
  const compounds = [], combinators = [];
  while (rest) {
    const parts = [];
    for (let part; (part = nextPart(rest, parts.length === 0)); ) { parts.push(part); rest = rest.slice(part.raw.length); }
    if (!parts.length) return undefined;
    compounds.push(parts);
    if (!rest) break;
    const combinator = /^\s*([>+~])\s*|^\s+/.exec(rest);
    if (!combinator) return undefined;
    combinators.push(combinator[1] ?? ' ');
    rest = rest.slice(combinator[0].length);
    if (!rest) return undefined;
  }
  return compounds.length ? {compounds, combinators} : undefined;
}

const serialize = ({compounds, combinators}) => compounds.map((parts, index) =>
  (index ? (combinators[index - 1] === ' ' ? ' ' : ` ${combinators[index - 1]} `) : '') + parts.map(part => part.raw).join('')).join('');

function statePart(part, states) {
  const simple = part.kind === 'not' ? part.inner : part;
  return (simple.kind === 'class' && states.has(simple.value)) || (simple.kind === 'attribute' && STATE_ATTRIBUTE.test(simple.value));
}

// The selector with state qualifiers removed from its subject (last) compound.
export function stateFreeSelector(selector, states) {
  const parsed = parseSelector(selector);
  if (!parsed) return undefined;
  const subject = parsed.compounds.at(-1);
  const kept = subject.filter(part => !statePart(part, states));
  if (!kept.length || kept.every(part => part.kind === 'pseudo' || part.kind === 'not')) return undefined;
  const compounds = [...parsed.compounds.slice(0, -1), kept];
  return {selector: serialize({compounds, combinators: parsed.combinators}), stripped: subject.length - kept.length};
}

// Elements whose static tag, id and classes satisfy the subject compound.
// Ancestor compounds, attributes and pseudo-classes are not evaluated, so a
// match is exact only for one compound of tag, id and class parts.
function staticMatches(selector, outline) {
  const parsed = parseSelector(selector);
  if (!parsed) return undefined;
  const subject = parsed.compounds.at(-1);
  const test = (element, part) => part.kind === 'tag' ? part.value === '*' || element.tag === part.value
    : part.kind === 'id' ? element.id === part.value : part.kind === 'class' ? element.classes.includes(part.value) : true;
  const indices = outline.elements.flatMap((element, index) => subject.every(part =>
    part.kind === 'not' ? !['tag', 'id', 'class'].includes(part.inner.kind) || !test(element, part.inner) : test(element, part)) ? [index] : []);
  return {indices, exact: parsed.compounds.length === 1 && subject.every(part => ['tag', 'id', 'class'].includes(part.kind))};
}

const contains = (outline, ancestor, index) => {
  for (let at = index, depth = 0; at >= 0 && depth < 1100; at = outline.elements[at].parent, depth += 1) if (at === ancestor) return true;
  return false;
};
function idLocator(outline, index) {
  const id = outline.elements[index].id;
  return typeof id === 'string' && SAFE_ID.test(id) && outline.elements.filter(element => element.id === id).length === 1
    ? {selector: `#${id}`} : undefined;
}
function headingLocator(outline, index) {
  const {name} = outline.elements[index];
  return name ? {role: 'heading', name, exact: true} : idLocator(outline, index);
}

// Returns {locator, basis, members} or undefined. basis: 'id' (the published
// id of the model's element), 'model' (the model's state-free locator, unique
// in the published page), 'unverified' (the model's state-free locator with
// no usable outline), 'heading' (the owner-named heading from the published
// page) or 'owner' (the owner's phrase as a heading name).
export function chooseTransitionTarget(request, {control, outline, phrase} = {}) {
  const clickAt = request.steps.findIndex(step => step.action === 'click');
  const states = new Set([...STATE_CLASS_WORDS, ...(outline?.stateClasses ?? [])]);
  const groups = new Map();
  request.steps.forEach((step, index) => {
    if (step.action === 'click' || (control && isDeepStrictEqual(step.locator, control))) return;
    const free = step.locator.selector !== undefined ? stateFreeSelector(step.locator.selector, states) : undefined;
    const base = free ? {selector: free.selector} : step.locator.role === 'heading' ? step.locator : undefined;
    if (!base) return;
    const key = JSON.stringify(base), group = groups.get(key) ?? {base, members: [], stripped: false, after: false};
    if (!group.members.some(member => isDeepStrictEqual(member, step.locator))) group.members.push(step.locator);
    group.stripped ||= Boolean(free?.stripped);
    group.after ||= clickAt >= 0 && index > clickAt;
    groups.set(key, group);
  });
  // A locator with a removed state qualifier names the element the model meant
  // to change; otherwise its assertions after the click, in order.
  const candidates = [...groups.values()].filter(group => group.stripped || group.after)
    .sort((left, right) => Number(right.stripped) - Number(left.stripped));
  const heading = outline?.headingIndex;
  for (const group of candidates) {
    if (!outline) return {locator: group.base, basis: 'unverified', members: group.members};
    let element, unique = false;
    if (group.base.selector !== undefined) {
      const matched = staticMatches(group.base.selector, outline);
      if (matched?.indices.length === 1) [element, unique] = [matched.indices[0], true];
      else if (matched?.indices.length > 1) {
        // Not unique without its state; one state-qualified form may still name one element.
        for (const member of group.members) {
          const narrowed = member.selector !== undefined ? staticMatches(member.selector, outline) : undefined;
          if (narrowed?.indices.length === 1) { element = narrowed.indices[0]; break; }
        }
      } else if (matched && heading === undefined) return {locator: group.base, basis: 'unverified', members: group.members};
    } else {
      const named = outline.elements.flatMap((item, index) => item.heading && item.name === group.base.name ? [index] : []);
      if (named.length === 1) [element, unique] = [named[0], true];
    }
    if (element === undefined || (heading !== undefined && !contains(outline, element, heading))) continue;
    const id = idLocator(outline, element);
    if (id) return {locator: id, basis: 'id', members: group.members};
    if (unique) return {locator: group.base, basis: 'model', members: group.members};
    if (heading !== undefined && headingLocator(outline, heading)) return {locator: headingLocator(outline, heading), basis: 'heading', members: group.members};
  }
  if (heading !== undefined && headingLocator(outline, heading)) return {locator: headingLocator(outline, heading), basis: 'heading'};
  if (phrase) return {locator: {role: 'heading', name: phrase, exact: true}, basis: 'owner'};
  return undefined;
}
