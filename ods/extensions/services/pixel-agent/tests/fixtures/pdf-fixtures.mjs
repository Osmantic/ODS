// Small PDF documents with known text, generated in the test itself so every
// byte is reviewable. Layouts cover what producers emit: a classic xref table
// with a standard font, a subset composite font whose glyph codes mean nothing
// without its ToUnicode map, kerned TJ arrays, and PDF 1.5 object and xref
// streams with a linearization dictionary.
import {deflateSync} from 'node:zlib';

const escapeLiteral = text => text.replace(/[\\()]/g, char => `\\${char}`);

// Serializes numbered objects, then an xref table and trailer, or an xref
// stream. `objects` entries: {num, dict, stream?} or {num, body}.
export function pdfFile(objects, {root, info, encrypt, xrefStream = false, compressed = [], header = '%PDF-1.7'} = {}) {
  const chunks = [Buffer.from(`${header}\n%\xe2\xe3\xcf\xd3\n`, 'latin1')];
  let length = chunks[0].length;
  const offsets = new Map();
  const push = buffer => { chunks.push(buffer); length += buffer.length; };
  for (const object of objects) {
    offsets.set(object.num, length);
    if (object.stream) {
      push(Buffer.from(`${object.num} 0 obj\n<< ${object.dict} /Length ${object.stream.length} >>\nstream\n`, 'latin1'));
      push(object.stream);
      push(Buffer.from('\nendstream\nendobj\n', 'latin1'));
    } else {
      push(Buffer.from(`${object.num} 0 obj\n${object.body}\nendobj\n`, 'latin1'));
    }
  }
  const size = Math.max(...objects.map(object => object.num), ...compressed.map(entry => entry.num), 0) + 2;
  const trailerKeys = `/Size ${size} /Root ${root} 0 R${info ? ` /Info ${info} 0 R` : ''}${encrypt ? ` /Encrypt ${encrypt} 0 R` : ''}`;
  if (xrefStream) {
    const num = size - 1;
    offsets.set(num, length);
    const rows = [];
    for (let i = 0; i < size; i++) {
      const row = Buffer.alloc(7);
      const inStream = compressed.find(entry => entry.num === i);
      if (offsets.has(i)) { row[0] = 1; row.writeUInt32BE(offsets.get(i), 1); }
      else if (inStream) { row[0] = 2; row.writeUInt32BE(inStream.stream, 1); row.writeUInt16BE(inStream.index, 5); }
      rows.push(row);
    }
    const data = deflateSync(Buffer.concat(rows));
    push(Buffer.from(`${num} 0 obj\n<< /Type /XRef /W [1 4 2] ${trailerKeys} /Filter /FlateDecode /Length ${data.length} >>\nstream\n`, 'latin1'));
    push(data);
    push(Buffer.from(`\nendstream\nendobj\nstartxref\n${offsets.get(num)}\n%%EOF\n`, 'latin1'));
  } else {
    const xref = length;
    const lines = ['xref', `0 ${size}`, '0000000000 65535 f '];
    for (let i = 1; i < size; i++) {
      lines.push(offsets.has(i) ? `${String(offsets.get(i)).padStart(10, '0')} 00000 n ` : '0000000000 65535 f ');
    }
    push(Buffer.from(`${lines.join('\n')}\ntrailer\n<< ${trailerKeys} >>\nstartxref\n${xref}\n%%EOF\n`, 'latin1'));
  }
  return new Uint8Array(Buffer.concat(chunks));
}

// A composite (Type0, Identity-H) subset font: glyph code = character code +
// 0x100, so text is only recoverable through the ToUnicode map, as with
// embedded subset fonts from office suites and browsers.
const glyphCode = char => char.codePointAt(0) + 0x100;
function toUnicodeMap(chars) {
  const codes = [...new Set(chars)].sort();
  const hex = (value, width) => value.toString(16).toUpperCase().padStart(width, '0');
  // Letters as one range each run; everything else as single characters.
  const single = codes.filter(char => !/[a-z]/.test(char)).map(char => `<${hex(glyphCode(char), 4)}> <${hex(char.codePointAt(0), 4)}>`);
  return ['/CIDInit /ProcSet findresource begin', '12 dict begin', 'begincmap',
    '/CMapName /Adobe-Identity-UCS def', '/CMapType 2 def',
    '1 begincodespacerange', '<0000> <FFFF>', 'endcodespacerange',
    '1 beginbfrange', '<0161> <017A> <0061>', 'endbfrange',
    `${single.length} beginbfchar`, ...single, 'endbfchar',
    'endcmap', 'CMapName currentdict /CMap defineresource pop', 'end', 'end'].join('\n');
}

function contentFor(lines, font) {
  const operations = ['BT', `/F1 11 Tf`, '1 0 0 1 72 720 Tm', '14 TL'];
  for (const [index, line] of lines.entries()) {
    if (index) operations.push('T*');
    if (font === 'type0') {
      operations.push(`<${[...line].map(char => glyphCode(char).toString(16).padStart(4, '0')).join('')}> Tj`);
    } else if (font === 'kerned') {
      // Words as separate strings; a -250 adjustment is the space between
      // them and -15 a kerning pair inside a word, as TeX and InDesign write.
      const words = line.split(' ').map(word => word.length > 3
        ? `(${escapeLiteral(word.slice(0, 2))}) -15 (${escapeLiteral(word.slice(2))})` : `(${escapeLiteral(word)})`);
      operations.push(`[${words.join(' -250 ')}] TJ`);
    } else {
      operations.push(`(${escapeLiteral(line)}) Tj`);
    }
  }
  operations.push('ET');
  return operations.join('\n');
}

// A document with one entry of `pages` (an array of text lines) per page.
export function textPdf(pages, {font = 'helvetica', compress = true, objectStreams = false, encrypted = false} = {}) {
  const objects = [];
  const pageNums = pages.map((_, index) => 10 + index * 2);
  const fontDict = font === 'type0'
    ? '<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+NotoSans-Regular /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>'
    : '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>';
  const dictObjects = [
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: `<< /Type /Pages /Kids [${pageNums.map(num => `${num} 0 R`).join(' ')}] /Count ${pages.length} >>`},
    {num: 3, body: fontDict},
    ...pageNums.map(num => ({num, body: `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${num + 1} 0 R >>`})),
  ];
  if (font === 'type0') {
    dictObjects.push({num: 4, body: '<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+NotoSans-Regular /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 500 /W [288 [260] 353 [520 540]] /CIDToGIDMap /Identity >>'});
  }
  const streamOf = text => compress
    ? {dict: '/Filter /FlateDecode', stream: deflateSync(Buffer.from(text, 'latin1'))}
    : {dict: '', stream: Buffer.from(text, 'latin1')};
  if (font === 'type0') objects.push({num: 6, ...streamOf(toUnicodeMap(pages.flat().join('')))});
  for (const [index, lines] of pages.entries()) objects.push({num: pageNums[index] + 1, ...streamOf(contentFor(lines, font))});
  if (encrypted) objects.push({num: 7, body: '<< /Filter /Standard /V 2 /R 3 /Length 128 /O <00> /U <00> /P -3904 >>'});
  const compressed = [];
  if (objectStreams) {
    // Linearization dictionary first, as web-optimized files begin.
    objects.unshift({num: 99, body: '<< /Linearized 1 /L 0 /O 10 /E 0 /N 1 /T 0 /H [0 0] >>'});
    const header = [], bodies = [];
    let offset = 0;
    for (const [index, object] of dictObjects.entries()) {
      header.push(`${object.num} ${offset}`);
      bodies.push(object.body);
      offset += object.body.length + 1;
      compressed.push({num: object.num, stream: 50, index});
    }
    const headerText = `${header.join(' ')}\n`;
    const data = deflateSync(Buffer.from(headerText + bodies.join('\n'), 'latin1'));
    objects.push({num: 50, dict: `/Type /ObjStm /N ${dictObjects.length} /First ${headerText.length} /Filter /FlateDecode`, stream: data});
  } else {
    objects.unshift(...dictObjects);
  }
  return pdfFile(objects, {root: 1, encrypt: encrypted ? 7 : undefined, xrefStream: objectStreams, compressed});
}

// A page whose only content is an image: nothing to read as text.
export function imageOnlyPdf() {
  const pixels = deflateSync(Buffer.alloc(16 * 16 * 3, 0x80));
  return pdfFile([
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'},
    {num: 3, body: '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>'},
    {num: 4, dict: '', stream: Buffer.from('q 200 0 0 200 72 500 cm /Im1 Do Q', 'latin1')},
    {num: 5, dict: '/Type /XObject /Subtype /Image /Width 16 /Height 16 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode', stream: pixels},
  ], {root: 1});
}

// ASCII85 (PDF 32000-1 §7.4.3) with the "z" shorthand for four zero bytes.
export function ascii85Encode(bytes) {
  const out = [];
  for (let i = 0; i < bytes.length; i += 4) {
    const size = Math.min(4, bytes.length - i);
    let value = 0;
    for (let k = 0; k < 4; k++) value = value * 256 + (k < size ? bytes[i + k] : 0);
    if (size === 4 && value === 0) { out.push('z'); continue; }
    const digits = [];
    for (let k = 0; k < 5; k++) { digits.unshift(String.fromCharCode(33 + (value % 85))); value = Math.floor(value / 85); }
    out.push(digits.slice(0, size + 1).join(''));
  }
  return `${out.join('')}~>`;
}

// One page whose content stream is ASCII85: its text, then `zeros` zero
// bytes written as "z" groups (one input byte for four output bytes).
export function ascii85Pdf(lines, {zeros = 0} = {}) {
  const content = Buffer.concat([Buffer.from(`${contentFor(lines, 'helvetica')}\n`, 'latin1'), Buffer.alloc(zeros)]);
  return pdfFile([
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'},
    {num: 3, body: '<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>'},
    {num: 4, dict: '/Filter /ASCII85Decode', stream: Buffer.from(ascii85Encode(content), 'latin1')},
    {num: 5, body: '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'},
  ], {root: 1});
}

// The review's first out-of-memory case (PR #6724): a 7.3 MB document whose
// one content stream is ASCII85 "z" groups, 29 MB of zero bytes decoded.
export const ascii85BombPdf = (zGroups = 7_300_000) => ascii85Pdf([], {zeros: zGroups * 4});

// The review's second case: Type0 fonts whose ToUnicode maps are bfrange
// lists of 65,536 targets each over codes that never repeat, about 15.6 MB
// per map decoded from a few KB. Each font on the page loads its own map.
export function cmapBombPdf({fonts = 3, ranges = 34} = {}) {
  const hex8 = value => value.toString(16).toUpperCase().padStart(8, '0');
  const list = `[${'<0041> '.repeat(65_536)}]`;
  const lines = Array.from({length: ranges}, (_, r) => `<${hex8(r * 65_536)}> <${hex8(r * 65_536 + 65_535)}> ${list}`);
  const cmap = ['begincmap', '1 begincodespacerange', '<00000000> <FFFFFFFF>', 'endcodespacerange',
    `${ranges} beginbfrange`, ...lines, 'endbfrange', 'endcmap'].join('\n');
  const names = Array.from({length: fonts}, (_, i) => `/F${i + 1}`);
  const objects = [
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'},
    {num: 3, body: `<< /Type /Page /Parent 2 0 R /Resources << /Font << ${names.map((name, i) => `${name} ${10 + i} 0 R`).join(' ')} >> >> /Contents 4 0 R >>`},
    {num: 4, dict: '', stream: Buffer.from(`BT ${names.map(name => `${name} 12 Tf <00000041> Tj`).join(' ')} ET`, 'latin1')},
    {num: 5, body: '<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Bomb /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>'},
  ];
  for (let i = 0; i < fonts; i++) {
    objects.push({num: 10 + i, body: `<< /Type /Font /Subtype /Type0 /BaseFont /Bomb /Encoding /Identity-H /DescendantFonts [5 0 R] /ToUnicode ${100 + i} 0 R >>`});
    objects.push({num: 100 + i, dict: '/Filter /FlateDecode', stream: deflateSync(Buffer.from(cmap, 'latin1'), {level: 9})});
  }
  return pdfFile(objects, {root: 1});
}

// Inside every parser bound (7.6 MB, five objects), but its 1.9 million
// empty dictionaries do not fit a 192 MB heap.
export function emptyDictionaryBombPdf(count = 950_000) {
  return pdfFile([
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'},
    {num: 3, body: '<< /Type /Page /Parent 2 0 R >>'},
    {num: 4, body: `[${'<<>>'.repeat(count)}]`},
    {num: 5, body: `[${'<<>>'.repeat(count)}]`},
  ], {root: 1});
}
