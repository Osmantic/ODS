// Fleet research prompts for pixel_ods_search_read replays.
//
// Result lists (URL and title only) are the first searches recorded in tower2
// Pixel sessions on 2026-09-25 (provider parallel-free) and the tower1 round
// 060 search from host_citation_verification.test.mjs. Page bodies are
// synthetic HTML shaped like the real pages (navigation, a dated event list
// with detail links, a specification table); no third-party page text is
// committed. Official values: RTX 5070 12 GB GDDR7 / 250 W; RX 9070 16 GB
// GDDR6 / 220 W.

export const EVENT_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.';
export const COMPONENT_PROMPT = 'Today is 2026-09-25. Compare the NVIDIA RTX 5070 and AMD RX 9070 for 1440p gaming. ' +
  'Research current official specifications and prices, open the source pages, and return JSON with sources.';

// tower2 session 1629ea4e, first search.
export const EVENT_SEARCH = {
  query: 'Philadelphia events September October 2026',
  results: [
    {url: 'https://www.philadelphiafed.org/calendar-of-events', title: 'Calendar of Events'},
    {url: 'https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia',
      title: 'Top Events & Festivals in Philadelphia: A Season-by-Season Guide | Visit Philadelphia'},
    {url: 'https://www.visitphilly.com/articles/philadelphia/events-festivals-2026',
      title: 'A Once-in-a-Lifetime Year: Philly’s Signature 2026 Events | Visit Philadelphia'},
    {url: 'https://philadelphiaevents.guide/october', title: 'Philadelphia Events in October 2026 | Event Calendar'},
    {url: 'https://americanarenas.com/city/philadelphia-events/september',
      title: 'Philadelphia Events September 2026 | Full Event Calendar'},
  ],
};

// tower1 round 060, first search: two venue listings.
export const VENUE_SEARCH = {
  query: 'Philadelphia events September October November 2026 concerts sports',
  results: [
    {url: 'https://www.xfinitymobilearena.com/events', title: 'Events | Xfinity Mobile Arena'},
    {url: 'https://www.lincolnfinancialfield.com/events/month/2026-09/', title: 'Events | Lincoln Financial Field'},
  ],
};

// tower2 session 57c1a232, first search.
export const COMPONENT_SEARCH = {
  query: 'NVIDIA GeForce RTX 5070 official specifications VRAM board power',
  results: [
    {url: 'https://www.pny.com/file%20library/company/support/product%20brochures/geforce%20graphics/english/rtx-5070-slim-12gb-dual-fan-oc-brochure.pdf',
      title: '[PDF] PNY GEFORCE RTX™ 5070 SLIM 12GB'},
    {url: 'https://www.asus.com/motherboards-components/graphics-cards/tuf-gaming/tuf-rtx5070-12g-gaming/techspec',
      title: 'ASUS TUF Gaming GeForce RTX™ 5070 12GB GDDR7 - Tech Specs'},
    {url: 'https://www.techspot.com/specs/gpu/305060-nvidia-geforce-rtx-5070.html', title: 'Nvidia GeForce RTX 5070 Specs | TechSpot'},
    {url: 'https://www.techpowerup.com/gpu-specs/geforce-rtx-5070.c4218', title: 'NVIDIA GeForce RTX 5070 Specs - GPU Database'},
    {url: 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/',
      title: 'GeForce RTX 5070 Family Graphics Cards | NVIDIA'},
  ],
};

export const RX9070_URL = 'https://www.amd.com/en/products/graphics/desktops/radeon/9000-series/amd-radeon-rx-9070.html';

export const GRITTY = 'https://www.xfinitymobilearena.com/events/detail/gritty-5k-presented-by-penn-medicine';
export const CAPITALS = 'https://www.xfinitymobilearena.com/events/detail/flyers-capitals-9-26-26';
export const TEDDY = 'https://www.xfinitymobilearena.com/events/detail/teddy-swims';
export const MOTIONLESS = 'https://www.xfinitymobilearena.com/events/detail/motionless-in-white';
export const RAMS = 'https://www.lincolnfinancialfield.com/events/los-angeles-rams-vs-philadelphia-eagles-2/';

const nav = (site) => `<header><nav><ul><li><a href="/">Home</a></li><li><a href="/events">Events</a></li>` +
  `<li><a href="/tickets">Tickets</a></li><li><a href="/plan-your-visit">Plan your visit</a></li>` +
  `<li><a href="https://www.facebook.com/${site}">Facebook</a></li></ul></nav></header>`;
const footer = `<footer><p>Philadelphia events and tickets. Privacy policy. Terms of use. © 2026</p></footer>`;
const filler = (n) => Array.from({length: n}, (_, i) => `<p>Section ${i}: arena news, parking and concessions information for fans.</p>`).join('');

export const EVENT_PAGES = {
  'https://www.xfinitymobilearena.com/events': '<html><head><title>Events | Xfinity Mobile Arena</title>' +
    '<script>window.dataLayer=[];for(var i=0;i<3;i++){}</script></head><body>' + nav('xfinitymobilearena') +
    '<h1>Upcoming Events</h1><ul>' +
    `<li><a href="/events/detail/gritty-5k-presented-by-penn-medicine">Gritty 5K Presented by Penn Medicine</a> Sat, Sep 26, 2026 7:30 AM</li>` +
    `<li><a href="/events/detail/flyers-capitals-9-26-26">Capitals vs. Flyers (Preseason)</a> Sat, Sep 26, 2026 5:00 PM</li>` +
    `<li><a href="/events/detail/teddy-swims">Teddy Swims: The Ugly Tour</a> Sat, Oct 10, 2026 7:00 PM</li>` +
    `<li><a href="/events/detail/motionless-in-white">Motionless In White</a> Sat, Oct 31, 2026 6:30 PM</li>` +
    `<li><a href="https://www.ticketmaster.com/xfinity-mobile-arena-tickets/venue/1">Buy on Ticketmaster</a></li>` +
    '</ul>' + filler(30) + footer + '</body></html>',
  'https://www.lincolnfinancialfield.com/events/month/2026-09/': '<html><head><title>Events | Lincoln Financial Field</title></head><body>' +
    nav('lincolnfinancialfield') + '<h1>September 2026</h1><ul>' +
    `<li><a href="/events/army-vs-temple/">Army vs. Temple</a> September 25, 2026 4:00 PM</li>` +
    `<li><a href="/events/los-angeles-rams-vs-philadelphia-eagles-2/">Los Angeles Rams vs. Philadelphia Eagles</a> October 4, 2026 1:00 PM</li>` +
    '</ul>' + footer + '</body></html>',
};

// Detail pages, for host verification and urls reads. The recorded web_fetch
// of the Xfinity detail pages got HTTP 406; the guarded reader reads them.
export const DETAIL_PAGES = {
  [GRITTY]: '<html><head><title>Gritty 5K Presented by Penn Medicine | Xfinity Mobile Arena</title></head><body>' + nav('x') +
    '<h1>Gritty 5K Presented by Penn Medicine</h1><p>Date</p><p>Sep 26, 2026</p><p>Event Starts</p><p>7:30 AM</p>' + footer + '</body></html>',
  [CAPITALS]: '<html><head><title>Capitals vs. Flyers (Preseason) | Xfinity Mobile Arena</title></head><body>' + nav('x') +
    '<h1>Capitals vs. Flyers (Preseason)</h1><p>Date</p><p>Sep 26, 2026</p><p>Event Starts</p><p>5:00 PM</p>' + footer + '</body></html>',
  [TEDDY]: '<html><head><title>Teddy Swims | Xfinity Mobile Arena</title></head><body>' + nav('x') +
    '<h1>Teddy Swims: The Ugly Tour</h1><p>Date</p><p>Oct 10, 2026</p><p>Event Starts</p><p>7:00 PM</p>' + footer + '</body></html>',
  [MOTIONLESS]: '<html><head><title>Motionless In White | Xfinity Mobile Arena</title></head><body>' + nav('x') +
    '<h1>Motionless In White - The Sweat and Blood Tour</h1><p>Date</p><p>Oct 31, 2026</p><p>Event Starts</p><p>6:30 PM</p>' + footer + '</body></html>',
  [RAMS]: '<html><head><title>Los Angeles Rams vs. Philadelphia Eagles | Lincoln Financial Field</title></head><body>' + nav('l') +
    '<h1>Los Angeles Rams vs. Philadelphia Eagles</h1><p>Sunday, October 4, 2026 at 1:00 PM ET</p>' + footer + '</body></html>',
};

const specRow = (name, value) => `<tr><td>${name}</td><td>${value}</td></tr>`;
export const COMPONENT_PAGES = {
  'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/': '<html><head><title>GeForce RTX 5070 Family Graphics Cards | NVIDIA</title></head><body>' +
    nav('nvidia') + '<h1>GeForce RTX 5070 Family</h1>' +
    Array.from({length: 40}, (_, i) => `<p>Powered by the NVIDIA Blackwell architecture and DLSS 4, feature ${i} brings new capabilities to gamers and creators.</p>`).join('') +
    '<h2>Specs</h2><table>' + specRow('NVIDIA CUDA Cores', '6144') + specRow('Boost Clock (GHz)', '2.51') +
    specRow('Memory Size', '12 GB') + specRow('Memory Type', 'GDDR7') + specRow('Total Graphics Power (W)', '250 W') +
    specRow('Required System Power (W)', '650 W') + '</table>' + footer + '</body></html>',
  'https://www.asus.com/motherboards-components/graphics-cards/tuf-gaming/tuf-rtx5070-12g-gaming/techspec':
    '<html><head><title>TUF-RTX5070-12G-GAMING Tech Specs | ASUS</title></head><body>' + nav('asus') +
    '<table>' + specRow('Graphic Engine', 'NVIDIA GeForce RTX 5070') + specRow('Video Memory', '12GB GDDR7') +
    specRow('Recommended PSU', '750W') + '</table>' + footer + '</body></html>',
  'https://www.techspot.com/specs/gpu/305060-nvidia-geforce-rtx-5070.html': {status: 403},
  'https://www.techpowerup.com/gpu-specs/geforce-rtx-5070.c4218': '<html><head><title>NVIDIA GeForce RTX 5070 Specs | TechPowerUp GPU Database</title></head><body>' +
    '<table>' + specRow('Memory Size', '12 GB') + specRow('Memory Type', 'GDDR7') + specRow('TDP', '250 W') + '</table></body></html>',
  [RX9070_URL]: '<html><head><title>AMD Radeon™ RX 9070 Graphics Card | AMD</title></head><body>' + nav('amd') +
    '<h1>AMD Radeon RX 9070</h1><table>' + specRow('Compute Units', '56') + specRow('Memory Size', '16 GB') +
    specRow('Memory Type', 'GDDR6') + specRow('Typical Board Power (Desktop)', '220 W') + '</table>' + footer + '</body></html>',
};
