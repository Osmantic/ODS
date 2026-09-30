// A stand-in for the SDK's HTML extractor with the same failure shape: lazy
// `[\s\S]*?` element patterns over the whole document, which take quadratic
// time on markup whose closing tags are missing.
const strip = value => value.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

async function extractFixture({html, extractMode}) {
  const title = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1];
  let text = html.replace(/<script[\s\S]*?<\/script>/gi, '');
  text = text.replace(/<a\s+[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_, href, body) =>
    extractMode === 'markdown' ? `[${strip(body)}](${href})` : strip(body));
  text = text.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, body) => `\n- ${strip(body)}`);
  text = text.replace(/<\/(?:p|div|h\d|ul|ol|tr|table|header|footer|section)>/gi, '\n').replace(/<\/td>/gi, ' ')
    .replace(/<[^>]+>/g, '').replace(/[ \t]+/g, ' ').trim();
  return text ? {text, ...(title ? {title: strip(title)} : {})} : null;
}

export {extractFixture as t};
