// Listing pages for the per-item source replays (tower1 round 091,
// 2026-09-25, integration build 2f89f3ea). The URLs, the recorded first
// search's result list and the final answer are the fleet run's; the page
// bodies are synthetic HTML shaped like the real pages (no third-party page
// text is committed): a season guide whose entry titles link to each entry's
// own site, with map, ticket, press and footer links around them, and an
// aggregator list whose entries link only to its own detail pages.

export const PER_ITEM_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia ' +
  'happening within the next 45 days. Actually search and open sources. For each give event title, exact date, venue ' +
  'and a direct official source URL. Exclude undated listings and past events. Explain any unavailable result ' +
  'honestly. Do not create files.';
// The same journey without a per-item or official source requirement.
export const PLAIN_PROMPT = 'Today is 2026-09-25. Search the live web for three public events in Philadelphia in the ' +
  'next 45 days, open the sources and tell me the dates and venues, with the URLs you used.';

export const GUIDE = 'https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia/';
export const AGGREGATOR = 'https://allevents.in/philadelphia/live-music';
export const DESIGN = 'https://designphiladelphia.org/pages/festival';
export const HALLOWEEN = 'https://easternstate.org/halloween';
export const RODEO = 'https://www.liacourascenter.com/events/detail/8-seconds-rodeo-2026';

// tower1 round 091, first pixel_ods_search_read (parallel-free).
export const R091_SEARCH = {
  query: 'Philadelphia events September October 2026',
  focus: 'event title, exact date, venue, official source URL for Philadelphia events in next 45 days',
  results: [AGGREGATOR, 'https://www.philadelphiafed.org/calendar-of-events', GUIDE.replace(/\/$/, ''),
    'https://americanarenas.com/city/philadelphia-events/september', 'https://philadelphiaevents.guide/october']
    .map(url => ({url})),
};

// tower1 round 091, the delivered answer (verbatim).
export const R091_ANSWER = `Here are three verified public events in Philadelphia within the next 45 days (through November 9, 2026):

---

**1. Phoebe Bridgers**
- **Date:** Sunday, September 27, 2026 at 7:00 PM
- **Venue:** Xfinity Mobile Arena, Philadelphia
- **Source:** https://allevents.in/philadelphia/live-music

---

**2. DesignPhiladelphia Festival**
- **Date:** October 1–11, 2026
- **Venue:** Various locations, Philadelphia (organized by Center for Architecture and Design)
- **Source:** https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia/

---

**3. Halloween Nights at Eastern State Penitentiary**
- **Date:** October 2 – November 7, 2026 (select dates)
- **Venue:** Eastern State Penitentiary, 2027 Fairmount Avenue, Philadelphia, PA
- **Source:** https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia/

---

All three events are sourced from official/public event listings. The Phoebe Bridgers concert is confirmed for a specific date, while DesignPhiladelphia and Halloween Nights are multi-day events with confirmed date ranges.`;

const footer = '<footer><p>Plan your trip. <a href="https://www.visitpa.com/">Visit Pennsylvania</a>. ' +
  '<a href="https://www.facebook.com/visitphilly">Facebook</a>. Privacy policy. © 2026</p></footer>';

// Ten dated entries; each title links to the entry's own site.
const ENTRIES = [
  ['Philadelphia Phillies at Citizens Bank Park', 'https://www.mlb.com/phillies/schedule/2026-09', 'Through Sunday, September 27, 2026 (select dates)'],
  ['Scarecrows in the Village at Peddler’s Village', 'https://peddlersvillage.com/event/scarecrows-in-the-village/', 'September 7 - October 25, 2026'],
  ['FallFest at Shady Brook Farm', 'https://shadybrookfarm.com/pages/fallfest', 'September 11 - October 30, 2026'],
  ['Philadelphia Fringe Festival', 'https://phillyfringe.org/', 'September 3 - 27, 2026'],
  ['DesignPhiladelphia Festival', DESIGN, 'October 1-11, 2026'],
  ['REEL October: The Philadelphia Film Festival at 35', 'https://filmadelphia.org/reel-october/', 'October 1-25, 2026 (dates vary by event)'],
  ['Halloween Nights at Eastern State Penitentiary', HALLOWEEN, 'October 2 – November 7, 2026 (select dates)'],
  ['Chinatown Night Market', 'https://chinatown-pcdc.org/2026-night-market/', 'Thursday, October 8, 2026 | 6-10 p.m.'],
  ['8 Second Rodeo at The Liacouras Center', RODEO, 'Saturday, October 10, 2026 | 7 p.m.'],
  ['Philadelphia Open Studio Tours', 'https://www.cfeva.org/philaopenstudios', 'October 10-18, 2026'],
];

export const GUIDE_PAGE = '<html><head><title>Top Events &amp; Festivals in Philadelphia: A Season-by-Season Guide | Visit Philadelphia</title></head><body>' +
  '<nav><ul><li><a href="/">Home</a></li><li><a href="/things-to-do/">Things to do</a></li></ul></nav>' +
  '<h1>Top Events &amp; Festivals in Philadelphia</h1>' +
  ENTRIES.map(([title, url, date], i) => `<h2><a href="${url}">${title}</a></h2>` +
    `<p>&mdash; Photo courtesy of the organizer</p><p>${date}</p>` +
    `<p>Entry ${i + 1} of the guide: what to expect, who it suits and how to plan the visit.` +
    (i === 6 ? ' It was named a top attraction by <a href="https://10best.usatoday.com/awards/haunted-attraction/">a national readers poll</a> this year.' : '') +
    (i === 8 ? ' Check the <a href="' + url + '">official site</a> for the schedule.' : '') + '</p>' +
    `<p>Where: <a href="https://maps.google.com/?cid=${1000 + i}">Philadelphia, PA</a></p>` +
    (i === 8 ? '<p><a href="https://www.ticketmaster.com/8-seconds-rodeo-tickets/artist/1">Buy tickets</a></p>' : '') +
    `<p><a href="${url}">read more</a></p>`).join('') + footer + '</body></html>';

// An aggregator's list: eight dated shows, links only to its own pages.
const SHOWS = [['Phoebe Bridgers', 'Sun 27 Sep 2026', 'Xfinity Mobile Arena'], ['Jane Remover', 'Fri 25 Sep 2026', 'Franklin Music Hall'],
  ['Jonas Brothers', 'Fri 02 Oct 2026', 'Xfinity Mobile Arena'], ['The Chicks', 'Wed 07 Oct 2026', 'The Met Philadelphia'],
  ['Gorgon City', 'Fri 25 Sep 2026', 'The Ave Live'], ['Dan And Shay', 'Sat 10 Oct 2026', 'Mann Center'],
  ['Rebecca Black', 'Fri 16 Oct 2026', 'Union Transfer'], ['Jessie Ware', 'Sun 11 Oct 2026', 'The Fillmore Philadelphia']];
export const AGGREGATOR_PAGE = '<html><head><title>Live Music in Philadelphia | Gigs, Bands &amp; Nights Out</title></head><body>' +
  '<h1>Live Music in Philadelphia</h1><ul>' +
  SHOWS.map(([name, date, venue]) => `<li><a href="/philadelphia/${name.toLowerCase().replace(/ /g, '-')}">${name}</a> ` +
    `${name} is happening on ${date} from 7:00 PM onwards at ${venue}, Philadelphia</li>`).join('') +
  '</ul><p><a href="https://support.allevents.in/">AllEvents support</a></p></body></html>';

// The entries' own pages.
export const OWN_PAGES = {
  [DESIGN]: '<html><head><title>Festival | DesignPhiladelphia</title></head><body><h1>Our Annual Festival</h1>' +
    '<p>DesignPhiladelphia Festival: October 1 - 11, 2026, at venues across Philadelphia.</p></body></html>',
  [HALLOWEEN]: '<html><head><title>Halloween Nights at Eastern State Penitentiary</title></head><body>' +
    '<h1>A Halloween Festival</h1><p>Halloween Nights: open select nights October 2 through Nov 7, 2026, at Eastern ' +
    'State Penitentiary, 2027 Fairmount Avenue, Philadelphia.</p></body></html>',
};
