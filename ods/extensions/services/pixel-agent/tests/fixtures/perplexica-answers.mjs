// Perplexica (Vane v1.12.2) answers measured on tower2 on 2026-09-25: the ODS
// slim pin, except the two latest-quality runs (slim-latest with Chromium),
// against ODS SearXNG and LiteLLM ods/current (qwen3-coder-next Q4_K_M), from
// /mnt/bulk/claude-tests/perplexica-research/measure/*.json. The prompts were
// the fleet journeys' own (event search, component research). Each answer is
// trimmed to its model-written lines that contain a link (the one answer with
// no link is kept whole); sources are the URLs
// Perplexica returned, in order. measured is the report's own count
// (findings/perplexica-fast-search.md): 24 of 25 answer links in speed and
// balanced mode, and 37 of 37 in quality mode, were not among the sources.
export const PERPLEXICA_MEASURED_RUNS = [
 {
  "run": "new-speed-event-1",
  "mode": "speed",
  "task": "event-search",
  "answer": "- **Official Source URL**: [https://fringearts.com/event/philadelphia-fringe-festival-2026/](https://fringearts.com/event/philadelphia-fringe-festival-2026/) [13]\n- **Official Source URL**: [https://phillypride365.org/ourfest/](https://phillypride365.org/ourfest/) [11]\n- **Official Source URL**: [https://www.easternstate.org/events/halloween-nights/](https://www.easternstate.org/events/halloween-nights/) [17]",
  "sources": [
   "https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia/",
   "https://thephiladelphiacitizen.org/events/",
   "https://blog.archive.org/events/",
   "https://www.eventbrite.com/d/pa--philadelphia/events/",
   "https://fifeweb.org/events/",
   "https://en.wikipedia.org/wiki/United_States_Semiquincentennial",
   "https://philadelphiaencyclopedia.org/subjects/events/",
   "https://humboldtkiters.org/october-2022-lincoln-city-kite-festival-videos/",
   "https://www.visitphilly.com/articles/philadelphia/top-halloween-events-and-activities-in-philadelphia/",
   "https://www.visitpittsburgh.com/events-festivals/halloween-events/",
   "https://phillygaycalendar.com/ourfest/",
   "https://180go.com/schedule.html",
   "https://phillyfringe.org/",
   "https://www.visitphilly.com/articles/philadelphia/events-festivals-2026/",
   "https://www.timeanddate.com/holidays/uk/halloween",
   "https://www.groupon.com/local/philadelphia/concerts",
   "https://www.kanofskygroup.com/blog/the-2021-philadelphia-halloween-attractions-you-cant-miss"
  ],
  "measured": {
   "answerUrls": 3,
   "notInSources": 3
  }
 },
 {
  "run": "new-speed-event-2",
  "mode": "speed",
  "task": "event-search",
  "answer": "- **Official Source**: [https://www.constitutioncenter.org/events philly-korean-festival-2026](https://www.constitutioncenter.org/events/philly-korean-festival-2026) *(Note: Visit Philadelphia featured this event in their fall event preview [9])*\n- **Caveat**: The main Philadelphia Auto Show typically occurs in January. A *fall* preview or satellite event is *plausible* but not yet confirmed on the official site ([philadelphiaautoshows.com](https://www.philadelphiaautoshows.com))—likely launching closer to October.\n- **Visit Philadelphia’s event calendar** ([https://www.visitphilly.com/events/](https://www.visitphilly.com/events/)) failed to load fully due to Playwright browser dependency issues during search [1][4].\n2. Visit the [**Visit Philadelphia Events Page**](https://www.visitphilly.com/events/) directly starting in early October, once more listings are populated.",
  "sources": [
   "https://www.visitphilly.com/articles/philadelphia/top-things-to-do-in-philadelphia-in-september/",
   "https://www.nps.gov/neph/planyourvisit/calendar.htm",
   "https://metrophiladelphia.com/stories/best-free-events-philly-summer,146648",
   "https://www.visitphilly.com/events/",
   "https://eosc.eu/events",
   "https://www.eventbrite.com/d/pa--philadelphia/events/",
   "https://fifeweb.org/events/",
   "https://philadelphiaencyclopedia.org/subjects/events/",
   "https://www.phillymag.com/things-to-do/fall-festivals-philly/",
   "https://www.ensembleartsphilly.org/tickets-and-events/free-events"
  ],
  "measured": {
   "answerUrls": 4,
   "notInSources": 3
  }
 },
 {
  "run": "new-balanced-event-1",
  "mode": "balanced",
  "task": "event-search",
  "answer": "- **Official Source:** [Visit Philadelphia – Hispanic Heritage Month Events](https://www.visitphilly.com/events/hispanic-heritage-month-celebration/) [^15]\n- **Official Source:** [Free Library of Philadelphia – Events Calendar](https://www.freelibrary.org/events/event-details/?id=98765) [^23]\n- **Official Source:** [Philadelphia Parks & Recreation – Fall Events Calendar](https://www.philapark.org/events/falls-festival-2026) [^22]\n| Hispanic Heritage Month Celebration | Oct 12, 2026 | Liberty Bell Center Plaza | [Visit Philly](https://www.visitphilly.com/events/hispanic-heritage-month-celebration/) |\n| Free Library – Holiday Book Sale (Preview) | Oct 22, 2026 | Central Library | [FreeLibrary.org](https://www.freelibrary.org/events/event-details/?id=98765) |\n| Falls Festival @ Fairmount Park | Nov 7, 2026 | Art Museum Plaza | [philapark.org](https://www.philapark.org/events/falls-festival-2026) |",
  "sources": [
   "https://www.visitphilly.com/articles/philadelphia/top-things-to-do-in-philadelphia-in-september/",
   "https://www.creativephl.org/creative-philadelphia-events/",
   "https://www.eventbrite.com/d/pa--philadelphia/events/",
   "https://thephiladelphiacitizen.org/events/",
   "https://philaathenaeum.org/programs-events/",
   "https://www.discoverphl.com/blog-post/annual-philadelphia-events/",
   "https://wacphila.org/public-programming/upcoming-events/",
   "http://www.ushistory.org/philadelphia/timeline/",
   "https://browse.sh/skills/phila.legistar.com/philly-council-events-nr6091",
   "https://philadelphiaencyclopedia.org/subjects/events/",
   "https://www.visitphilly.com/articles/philadelphia/top-events-and-festivals-in-philadelphia/",
   "https://www.visitphilly.com/articles/philadelphia/events-festivals-2026/",
   "https://blog.archive.org/events/",
   "https://www.visitphilly.com/articles/philadelphia/upcoming-concerts/",
   "https://www.visitphilly.com/articles/philadelphia/hispanic-heritage-month-in-philadelphia/",
   "https://www.visitphilly.com/tag/events/",
   "https://en.wikipedia.org/wiki/United_States_Semiquincentennial",
   "https://www.phila.gov/services/culture-recreation/activities-and-events/",
   "https://www.phila.gov/programs/ring-it-on/",
   "https://phillyfringe.org/",
   "https://www.phila.gov/programs/",
   "https://www.phila.gov/parks-rec-finder/",
   "https://freelibraryfoundation.org/events/",
   "https://en.wikipedia.org/wiki/Category:Organized_events_in_Pennsylvania",
   "https://www.visitphilly.com/media-center/",
   "https://www.visitphilly.com/",
   "https://en.wikipedia.org/wiki/Philadelphia_Fringe_Festival",
   "https://kcfringe.org/2026-events/",
   "https://metrophiladelphia.com/stories/philadelphia-fringe-festival-2026,153896",
   "https://fringearts.com/artists/",
   "https://intrepidtheatre.com/festivals/fringe-festival/",
   "https://www.songkick.com/metro-areas/5202-us-philadelphia",
   "https://www.edinburghfestivalcity.com/festivals/edinburgh-festival-fringe",
   "https://www.fringefest.com/",
   "https://fringearts.com/",
   "https://www.phila.gov/the-latest/all-events/",
   "https://castateparks.wordpress.com/2023/10/",
   "https://www.linkedin.com/company/free-library-of-philadelphia-foundation",
   "https://www.floridastateparks.org/events",
   "https://www.freelibrary.org/"
  ],
  "measured": {
   "answerUrls": 3,
   "notInSources": 3
  }
 },
 {
  "run": "new-speed-component-1",
  "mode": "speed",
  "task": "component-research",
  "answer": "          \"https://www.nvidia.com/en-us/geforce/graphics-cards/rtx-5070/\",\n          \"https://www.techpowerup.com/gpu-specs/geforce-rtx-5070.c4128\"\n        \"sourceUrl\": \"https://www.newegg.com/p/N82E16814487627\"\n          \"https://www.amd.com/en/products/graphic-cards/radeon-7000-series/radeon-rx-9070.html\",\n          \"https://www.techpowerup.com/gpu-specs/radeon-rx-9070.c4127\"\n        \"sourceUrl\": \"https://www.amazon.com/dp/B0DQKJL9XH\"\n    \"sourceUrl\": \"https://www.tomshardware.com/reviews/nvidia-rtx-5070-vs-amd-rx-9070\",",
  "sources": [
   "https://www.tomshardware.com/pc-components/gpus/nvidia-rtx-5070-vs-amd-rx-9070-face-off",
   "https://www.tomshardware.com/pc-components/gpus/geforce-rtx-50-series-gpu-prices-spike-as-much-as-39-percent-as-blackwell-price-hikes-hit-the-us-rtx-5070-gets-a-36-percent-hike-rtx-5060-up-27-percent-at-the-median-of-newegg-listings",
   "https://www.tomshardware.com/pc-components/gpus/nvidia-geforce-rtx-5070-review-founders-edition",
   "https://www.datart.cz/graficka-karta-gigabyte-geforce-rtx-5070-windforce-oc-sff-12g-gv-n5070wf3oc-12gd/1.html",
   "https://www.tomshardware.com/pc-components/gpus/nvidia-geforce-rtx-5070-ti-review-asus",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-xt-review/4",
   "https://www.tsbohemia.cz/sapphire-pure-radeon-rx-9070-xt-gaming-16gb_d542709",
   "https://www.amd.com/en/products/graphics/desktops/radeon.html",
   "https://www.techpowerup.com/gpu-specs/geforce-rtx-5070.c4218",
   "https://www.mironet.cz/graficke-karty+fc14402/?PgID=7",
   "https://www.techradar.com/computing/gpu/gigabyte-radeon-rx-9070-xt-gaming-oc-ice-16gb-review-excellent-performance-from-a-standout-graphics-card",
   "https://www.datart.cz/graficka-karta-asus-tuf-gaming-radeon-rx-9070-xt-oc-16g-90yv0l70-m0na00.html",
   "https://www.tomshardware.com/pc-components/xfx-radeon-rx-9070-xt-drops-to-its-lowest-price-of-the-summer-save-usd90-on-amds-flagship-rdna-4-graphics-card",
   "https://allegro.cz/produkt/graficka-karta-gigabyte-geforce-rtx-5070-ti-windforce-oc-v2-16gb-gddr7-75a5be9e-3468-4e9c-87a2-3bf89b22303d",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-xt-review",
   "https://www.ebay.com/shop/amd-rx-9070-xt?_nkw=amd+rx+9070+xt",
   "https://www.tsbohemia.cz/herni-graficke-karty-s-cipem-amd_c51215",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-gre-review",
   "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.nvidia.com/cs-cz/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.tomshardware.com/reviews/best-gpus,4380.html",
   "https://www.tsbohemia.cz/herni-graficke-karty-nvidia-geforce-rtx-5070_c66314",
   "https://www.alza.cz/graficke-karty-recenze/18842862.htm"
  ],
  "measured": {
   "answerUrls": 7,
   "notInSources": 7
  }
 },
 {
  "run": "new-speed-component-2",
  "mode": "speed",
  "task": "component-research",
  "answer": "Hmm, sorry I could not find any relevant information on this topic. Would you like me to search again or ask something else?",
  "sources": [
   "https://www.tomshardware.com/pc-components/gpus/nvidia-geforce-rtx-5070-ti-review-asus",
   "https://www.tsbohemia.cz/gigabyte-geforce-rtx-5070-ti-windforce-oc-v2-16g_d573296",
   "https://www.amd.com/zh-tw/products/graphics/desktops/radeon.html",
   "https://www.zbozi.cz/vyrobek/asus-tuf-gaming-amd-radeon-rx-9070-xt-oc-16-gb-90yv0l70-m0na00/",
   "https://www.kaufland.cz/product/529663439/",
   "https://www.tsbohemia.cz/sapphire-pure-radeon-rx-9070-xt-gaming-16gb_d542709",
   "https://www.nvidia.com/de-de/geforce/graphics-cards/50-series/",
   "https://www.amd.com/en/partner/browse-by-product/radeon-resources.html",
   "https://www.amd.com/zh-cn/products/graphics/desktops/radeon.html",
   "https://www.nvidia.com/cs-cz/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.nvidia.com/en-us/geforce/news/rtx-5050-desktop-gpu-and-laptops/",
   "https://www.amd.com/en/support/download/drivers.html",
   "https://www.amd.com/en/products/graphics/desktops/radeon/9000-series/amd-radeon-rx-9050.html",
   "https://www.nvidia.com/en-eu/geforce/campaigns/back-to-school/"
  ],
  "measured": {
   "answerUrls": 0,
   "notInSources": 0
  }
 },
 {
  "run": "new-balanced-component-1",
  "mode": "balanced",
  "task": "component-research",
  "answer": "{\"observedAt\":\"2026-09-25T11:53:42.272Z\",\"components\":[{\"name\":\"RTX 5070\",\"primarySpecs\":{\"vramGB\":12,\"boardPowerW\":220,\"sourceUrls\":[\"https://www.nvidia.com/en-us/geforce/graphics-cards/rtx-5070/\",\"https://www.nvidia.com/content/dam/en-zz/Solutions/geforce/rtx-50-series/NVIDIA-GeForce-RTX-5070-Spec.pdf\"]},\"retail\":{\"retailer\":\"Micro Center\",\"sku\":\"GV-N5070WF3OC-12GD (Gigabyte GeForce RTX 5070 Windforce OC)\",\"condition\":\"new\",\"price\":579.99,\"currency\":\"USD\",\"inStock\":false,\"observedAt\":\"2026-09-25T11:53:42.272Z\",\"locationShippingCaveat\":\"Online only; ships from multiple US warehouses. Tax applies in-state. Current backorder estimated for 2–4 weeks.\",\"sourceUrl\":\"https://www.microcenter.com/product/6706758/geforce-rtx-5070-windforce-oc-12gb-gddr7-pcie-50\"}},{\"name\":\"RX 9070\",\"primarySpecs\":{\"vramGB\":12,\"boardPowerW\":230,\"sourceUrls\":[\"https://www.amd.com/en/products/graphics/radeon-9000/radeon-rx-9070.html\",\"https://download.amd.com/documentation/12729_Radeon_RX_9070_Spec.pdf\"]},\"retail\":{\"retailer\":\"Newegg\",\"sku\":\"SAP-R9070XT-16G (Sapphire Pulse Radeon RX 9070 16GB GDDR6)\",\"condition\":\"new\",\"price\":549.99,\"currency\":\"USD\",\"inStock\":true,\"observedAt\":\"2026-09-25T11:53:42.272Z\",\"locationShippingCaveat\":\" Ships from Newegg warehouse (NJ). Tax applicable in certain states. No backorder noted as of observation.\",\"sourceUrl\":\"https://www.newegg.com/sapphire-radeon-rx-9070-pulse-16g-gddr6/p/1XK-000T-001W3\"}}],\"benchmark\":{\"publisher\":null,\"sourceUrl\":null,\"resolution\":\"2560x1440\",\"testDate\":null,\"metric\":null,\"results\":null,\"methodCaveats\":\"No independent benchmark article found comparing both GPUs in identical 1440p test conditions at time of observation.\"}}\n[1]: https://www.nvidia.com/en-us/geforce/graphics-cards/rtx-5070/\n[2]: https://www.nvidia.com/content/dam/en-zz/Solutions/geforce/rtx-50-series/NVIDIA-GeForce-RTX-5070-Spec.pdf\n[3]: https://www.microcenter.com/product/6706758/geforce-rtx-5070-windforce-oc-12gb-gddr7-pcie-50\n[4]: https://download.amd.com/documentation/12729_Radeon_RX_9070_Spec.pdf\n[5]: https://www.sapphiretech.com/en-us/products/radeon-rx-9070-pulse-12g-gddr6/11289-01\n[6]: https://www.newegg.com/sapphire-radeon-rx-9070-pulse-16g-gddr6/p/1XK-000T-001W3\n[7]: https://www.tomshardware.com/reviews/amd-radeon-rx-9070,6392.html",
  "sources": [
   "https://www.ebay.com/shop/rtx-5070-ti?_nkw=rtx+5070+ti",
   "https://www.mironet.cz/msi-geforce-rtx-5070-12g-ventus-2x-oc-white-12gb-gddr7-pcie-3x-dp-hdmi+dp733670/",
   "https://www.zbozi.cz/vyrobek/gigabyte-geforce-rtx-5070-windforce-oc-sff-12-gb-gv-n5070wf3oc-12gd/",
   "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.nvidia.com/cs-cz/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.tomshardware.com/pc-components/gpus/nvidia-rtx-5070-vs-amd-rx-9070-face-off",
   "https://www.kaufland.cz/product/534599742/",
   "https://www.onlineshop.cz/pc-sestavy/hal3000-alfa-gamer-micro-aio-amd-ryzen-7-9800x3d-32gb-ddr5-rtx-5070-ti-2tb-pcie4-ssd-wifi-w11-pchs28-1287370P.html",
   "https://www.techradar.com/live/news/where-to-buy-amd-radeon-rx-9070-xt-and-rx-9070-these-are-the-best-retailers-in-the-us-and-uk",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-gre-review",
   "https://www.zbozi.cz/vyrobek/asus-tuf-gaming-amd-radeon-rx-9070-xt-oc-16-gb-90yv0l70-m0na00/",
   "https://www.ebay.com/shop/amd-rx-9070-xt?_nkw=amd+rx+9070+xt",
   "https://www.tsbohemia.cz/sapphire-pure-radeon-rx-9070-xt-gaming-16gb_d542709",
   "https://www.techradar.com/computing/gpu/gigabyte-radeon-rx-9070-xt-gaming-oc-ice-16gb-review-excellent-performance-from-a-standout-graphics-card",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-xt-review",
   "https://www.zbozi.cz/vyrobek/powercolor-reaper-amd-radeon-rx-9070-rx9070-16g-a/",
   "https://www.tsbohemia.cz/graficke-karty-amd-radeon-rx-9070_c66609",
   "https://www.amd.com/en/products/graphics/desktops/radeon.html",
   "https://www.datart.cz/graficka-karta-sapphire-pure-amd-radeon-rx-9060-xt-gaming-16gb-11350-02-20g"
  ],
  "measured": {
   "answerUrls": 8,
   "notInSources": 8
  }
 },
 {
  "run": "latest-quality-component-1",
  "mode": "quality",
  "task": "component-research",
  "answer": "| **RTX 5070** | **12 GB GDDR7** | **250 W** | [GeForce RTX 5070 official spec sheet](https://www.nvidia.com/en-us/geforce/graphics-cards/rtx-5070/specifications/) [4][6] |\n| **RX 9070** | **16 GB GDDR6** | **220 W** | [AMD Radeon RX 9070 technical brief](https://www.amd.com/en/products/graphics/radeon-7000/radeon-rx-9070.html) [7][8] |\n- **SKU**: [ASUS Prime NVIDIA GeForce RTX 5070 (ASUS-PRIME-RTX5070)](https://www.newegg.com/p/N82E16814487622)\n- **SKU**: [PowerColor Fighter RX 9070 (PCX-9070-F2-16GHD6-3DP)](https://www.newegg.com/p/N82E16814145910)\nAfter reviewing dozens of high-traffic benchmark articles—including [Tom’s Hardware](https://www.tomshardware.com), [GamersNexus](https://www.gamersnexus.net), [TechPowerUp](https://www.techpowerup.com), [PCMag](https://www.pcmag.com), and [AnandTech](https://www.anandtech.com)—no such unified benchmark has been published as of 2026-09-25.\n| [Tom’s Hardware – RTX 5070 vs RX 9070 XT Face Off (2026-04-18)](https://www.tomshardware.com/gpus/nvidia-rtx-5070-vs-amd-rx-9070-xt) | 2026-04-18 | **RTX 5070** and **RX 9070 XT** (not RX 9070) | 1440p | No RX 9070 (non-XT) tested; RTX 5070 vs RX 9070 XT only [1]. |\n| [GamersNexus – Radeon RX 9070 Deep Dive (2026-04-02)](https://www.gamersnexus.net/gpus/6548-amd-radeon-rx-9070-deep-dive-benchmarks) | 2026-04-02 | RX 9070 only | 1440p/4K | No RTX 5070 in same test [7]. |\n| [PCMag – GeForce RTX 5070 Review (2026-03-25)](https://www.pcmag.com/picks/nvidia-geforce-rtx-5070-review) | 2026-03-25 | RTX 5070 only | 1440p | No RX 9070 [4]. |\n          \"https://www.nvidia.com/en-us/geforce/graphics-cards/rtx-5070/specifications/\"\n        \"sourceUrl\": \"https://www.newegg.com/p/N82E16814487622\"\n          \"https://www.amd.com/en/products/graphics/radeon-7000/radeon-rx-9070.html\"\n        \"sourceUrl\": \"https://www.newegg.com/p/N82E16814145910\"",
  "sources": [
   "https://www.tomshardware.com/pc-components/gpus/nvidia-rtx-5070-vs-amd-rx-9070-face-off",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-xt-review/2",
   "https://www.tomshardware.com/pc-components/gpus/amd-radeon-rx-9070-xt-review",
   "https://www.nvidia.com/cs-cz/geforce/graphics-cards/50-series/rtx-5070-family/",
   "https://www.nvidia.com/en-us/geforce/news/geforce-rtx-5060-ti-game-ready-driver/",
   "https://gamepress.cz/nvidia-uplne-specifikace-geforce-rtx-5070-ti-rtx-5070/",
   "https://svethardware.cz/article/amd-uvadi-radeon-rx-9070-xt-s-vykonem-rtx-5070-ti-za-599-usd",
   "https://diit.cz/clanek/radeony-rx-9070-xt-ohlaseny-technologicke-novinky-specifikace-architektura",
   "http://www.ddworld.cz/pc-a-komponenty/graficke-karty/amd-radeon-rx-9070-xt-16gb-test-a-recenze-konecne-dobreho-mainstreamu-nove-generace-10.html",
   "https://www.tomshardware.com/pc-components/gpus/nvidia-geforce-rtx-5070-review-founders-edition",
   "https://www.gamesradar.com/hardware/desktop-pc/where-to-buy-rtx-5070/",
   "https://www.tomshardware.com/pc-components/gpus/geforce-rtx-50-series-gpu-prices-spike-as-much-as-39-percent-as-blackwell-price-hikes-hit-the-us-rtx-5070-gets-a-36-percent-hike-rtx-5060-up-27-percent-at-the-median-of-newegg-listings",
   "https://www.techradar.com/computing/gpu/amd-radeon-rx-9070",
   "https://www.newegg.com/insider/news-rtx-5070-vs-rtx-5080-best-gpu-for-your-gaming-desktop/",
   "https://www.amazon.com/dp/B0DS6V1YSY",
   "https://www.newegg.com/insider/news-gpu-buying-guide-how-to-choose-the-right-graphics-card/",
   "https://www.newegg.com/insider/is-8gb-of-vram-still-enough-in-2026/",
   "https://www.datart.cz/graficka-karta-sapphire-nitro-radeon-rx-9070-xt-16g-oc-11348-01-20g.html",
   "https://www.techradar.com/live/news/where-to-buy-amd-radeon-rx-9070-xt-and-rx-9070-these-are-the-best-retailers-in-the-us-and-uk"
  ],
  "measured": {
   "answerUrls": 12,
   "notInSources": 12
  }
 },
 {
  "run": "latest-quality-event-1",
  "mode": "quality",
  "task": "event-search",
  "answer": "  [https://6abc.com/event Phila Thanksgiving Day Parade](https://6abc.com/event Phila Thanksgiving Day Parade)\n  (See also: [https://www.visitphilly.com/events/philiadelphia-thanksgiving-day-parade](https://www.visitphilly.com/events/philiadelphia-thanksgiving-day-parade))\n  [https://www.centercityphila.org/events/center-city-christmas-village/](https://www.centercityphila.org/events/center-city-christmas-village/)\n  [https://myphillypark.org/events/love-your-park-fall-weekend/](https://myphillypark.org/events/love-your-park-fall-weekend/)\n  [https://www.phila.gov/departments/philadelphia-parks-recreation/volunteer/](https://www.phila.gov/departments/philadelphia-parks-recreation/volunteer/)\n- **Source**: [https://www.inquirer.com/food/festival](https://www.inquirer.com/food/festival)\n- **Source**: [https://www.phillymarathon.org](https://www.phillymarathon.org)\n      - **Source**: [https://www.philamuseum.org/craftshow](https://www.philamuseum.org/craftshow)\n| 1 | **Philadelphia Thanksgiving Day Parade** | Nov 26, 2026 | Benjamin Franklin Parkway | ✅ Free public viewing | [6abc.com Parade Page](https://6abc.com/event%20Phila%20Thanksgiving%20Day%20Parade) |\n| 2 | **Center City Christmas Village** | Nov 21 – Dec 27, 2026 | LOVE Park & City Hall | ✅ Free entry; pay-as-you-go for goods/rides | [Center City District](https://www.centercityphila.org/events/center-city-christmas-village/) |\n| 3 | **Love Your Park Fall Weekend** | Nov 13–15, 2026 | Citywide parks | ✅ Free, volunteer-based | [Fairmount Park Conservancy](https://myphillypark.org/events/love-your-park-fall-weekend/) |\n| 4 | **Philadelphia Museum of Art Craft Show** | Nov 6–8, 2026 | Pennsylvania Convention Center | 🎟️ Admission ~$15–20 (discounts available) | [Philadelphia Museum of Art](https://www.philamuseum.org/craftshow) |",
  "sources": [
   "https://libwww.freelibrary.org/calendar/",
   "https://www.visitphilly.com/articles/philadelphia/top-things-to-do-in-philadelphia-in-september/",
   "https://www.visitphilly.com/",
   "https://devopsdays.org/events/2026-philadelphia/welcome/",
   "https://www.visitphilly.com/articles/philadelphia/hispanic-heritage-month-in-philadelphia/",
   "https://www.phila.gov/",
   "https://phillyfringe.org/",
   "https://www.discoverphl.com/blog-post/annual-philadelphia-events/",
   "https://freelibraryfoundation.org/events/",
   "https://calendarcute.com/thanksgiving-day/",
   "https://www.philadelphiamarathon.com/",
   "https://marching.com/events/6abc-philadelphia-thanksgiving-day-parade/",
   "https://www.philamuseum.org/",
   "https://www.philamuseum.org/public-programs",
   "https://www.pmacraftshow.org/application",
   "https://www.phila.gov/parks-rec-finder/",
   "https://www.phl.org/welcome2026",
   "https://www.freelibrary.org/",
   "https://www.visitphilly.com/2026-philadelphia/",
   "https://philadelphia.today/",
   "https://www.visitphilly.com/things-to-do/attractions/kimmel-center-for-the-performing-arts/",
   "https://www.visitphilly.com/events/fall-events/",
   "https://www.linkedin.com/company/visit-philadelphia",
   "https://www.visitphilly.com/articles/philadelphia/events-festivals-2026/",
   "https://www.visitphilly.com/things-to-do/attractions/christmas-village-in-philadelphia/",
   "https://www.philachristmas.com/index.html",
   "https://flynnco.com/tag/veteransday/",
   "https://www.visitphilly.com/articles/philadelphia/best-holiday-markets-and-pop-ups-philadelphia/",
   "https://www.visitphilly.com/things-to-do/events/electrical-spectacle-holiday-light-show-at-franklin-square/",
   "https://www.visitphilly.com/things-to-do/events/philadelphia-chinese-lantern-festival-at-franklin-square/",
   "https://foodfest.inquirer.com/",
   "https://www.eventbrite.com/d/pa--philadelphia/events/",
   "https://fi.edu/en/events-calendar",
   "https://fi.edu/en",
   "https://thephiladelphiacitizen.org/good-citizen-calendar/",
   "https://www.booking.com/booking-home/city/us/philadelphia.html",
   "https://www.cradlestocrayons.org/philadelphia/event-calendar/",
   "https://loveyourpark.org/",
   "https://www.phila.gov/departments/philadelphia-parks-recreation/",
   "https://myphillypark.org/highlights-from-love-your-park-fall-weekend/",
   "https://myphillypark.org/support/volunteer/"
  ],
  "measured": {
   "answerUrls": 9,
   "notInSources": 9
  }
 },
 {
  "run": "new-quality-event-1",
  "mode": "quality",
  "task": "event-search",
  "answer": "- **Official Source URL**: https://filmlinc.org/festivals/phila-film-festival-fall-2026\n- **Official Source URL**: https://phillybeerweek.com/neighborbrews-2026\n   - *Source*: https://www.freelibrary.org/events/stories-in-the-garden\n   - *Source*: https://philamuseum.org/events/family-festival-art-and-sound.html\n   - *Source*: https://kimmelculturalcampus.org/events/broadway-night\n   - *Source*: https://phillyflowershow.org\n   - *Source*: https://www.phila.gov/departments/phila-parks-recreation/events\n  - *Official Site*: https://phillybeerweek.com\n  - *Official Page*: https://events.upenn.edu\n- VisitPhilly Events Calendar: https://www.visitphilly.com/events\n- Philly Beer Week Calendar: https://phillybeerweek.com/events\n- Free Library Events: https://www.freelibrary.org/events\n1. Philadelphia Film Society. *Philadelphia Film Festival – Fall Edition*. Filmlinc.org. https://filmlinc.org/festivals/phila-film-festival-fall-2026\n2. Philly Beer Week. *Neighborbrews: A Philly Beer Week Pre-Event Series*. Phillybeerweek.com. https://phillybeerweek.com/neighborbrews-2026\n3. Free Library of Philadelphia. *Stories in the Garden*. Freelibrary.org. https://www.freelibrary.org/events/stories-in-the-garden\n5. Kimmel Cultural Campus. *Upcoming Events*. Kimmelculturalcampus.org. https://kimmelculturalcampus.org/events\n6. Pennsylvania Horticultural Society. *Philly Flower Show 2027*. Phillyflowershow.org. https://phillyflowershow.org\n8. Philly Beer Week Inc. *Press Release: Philly Beer Week 2026 Announced*. Phillybeerweek.com. September 17, 2026. https://phillybeerweek.com/press/2026-press-release\n9. Free Library of Philadelphia. *2025 Winter Writers Series Archive*. Freelibrary.org. November 4, 2025. https://www.freelibrary.org/events/2025-winter-writers-series\n10. University of Pennsylvania. *Winter Arts Festival 2025*. events.upenn.edu. https://events.upenn.edu/event/winter-arts-festival-2025",
  "sources": [],
  "measured": {
   "answerUrls": 16,
   "notInSources": 16
  }
 }
];
