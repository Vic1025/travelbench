# TravelBench — Seasonal Windows Design
*Internal reference · Phase 3 city data generation*

---

## Design Principles

### What a seasonal window is
A 7-day date range within 2026 chosen to:
- Contain at least one culturally significant local event or date
- Represent a meaningfully distinct character from the city's other windows
- Allow a 5-day trip to fit cleanly inside it

### How windows affect venue generation
For each city, PLAN_VENUES runs once per *distinct* seasonal pool.
Cities with season-invariant venues (London, Tokyo) run it once and
reuse the base pool across windows — only hours, events, and ticket
availability vary by window.

Cities with season-dependent venues (Hokkaido) run PLAN_VENUES
separately per window. After generation, venues that appear across
multiple windows are identified and flagged with a `seasonal_windows`
list — the data-generation agent for those venues receives a note
on which windows the venue operates in and what changes between them.

### What the window character feeds into
- PLAN_VENUES prompt: primary context for venue selection
- Per-venue agent: informs plausible hours, events, source doc dates
- Conditional wrong info: a source written in one window carrying
  incorrect information for another window is the canonical seasonal trap
- Ticket availability: sold-out slots concentrated on anchor event dates

### Conditional wrong info — three specific trap patterns per window
Each window defines three distinct wrong-info opportunities:
1. **Hours trap** — a source written outside this window states normal
   hours that don't apply during the window (reduced Christmas hours,
   extended carnival hours, ski resort summer closure)
2. **Access trap** — a source states a venue is freely accessible when
   during this window it requires advance booking, a ticket, or is
   closed entirely
3. **Character trap** — a source describes the venue's typical atmosphere
   in a way that's misleading for this window (a "quiet neighbourhood
   café" that's surrounded by carnival crowds, a "peaceful park walk"
   that's a ski slope in winter)

---

## LONDON, United Kingdom
*Season-invariant pool: one venue set, four windows*
*Base pool: 50 venues across all windows; hours/events/tickets vary*

### Window 1 — Spring: Easter Week
**Dates:** 2026-04-02 (Thu) to 2026-04-08 (Wed)
**Anchor events:**
- Good Friday 2026-04-03 (Bank Holiday)
- Easter Sunday 2026-04-05
- Easter Monday 2026-04-06 (Bank Holiday)

**Character:**
London's first real outdoor weekend of the year. Good Friday and Easter
Monday are bank holidays — most venues run reduced hours or close
entirely on those two days, with Saturday and Sunday at peak busyness.
Borough Market and Portobello Road heave with weekend crowds. Parks
fill with families. Popular restaurants run fixed Easter menus requiring
advance booking. Churches hold public services that affect access in
certain areas. The contrast between the two bank-holiday closures and
the busy market days in between is the defining planning challenge.

**Conditional wrong info traps:**
1. *Hours trap* — a Yelp listing or blog written in June shows normal
   Monday hours; the venue is actually closed Easter Monday. The source
   has no awareness of bank holiday closures.
2. *Access trap* — a source says "walk-ins always welcome" for a
   restaurant that runs a fixed Easter menu requiring booking 3+ weeks
   ahead during this window.
3. *Character trap* — a source describes a normally quiet neighbourhood
   pub as "a relaxed local spot" when it is actually packed wall-to-wall
   for the Easter bank holiday weekend with no table service.

**Planning implications:**
- Any trip starting Thu 2 Apr has one normal day before the bank holiday
- Fri and Mon are high-risk days for venue closures
- Outdoor venues (parks, markets) are weather-dependent but typically good

---

### Window 2 — Late Spring: Bank Holiday Weekend
**Dates:** 2026-05-23 (Sat) to 2026-05-29 (Fri)
**Anchor events:**
- Spring Bank Holiday 2026-05-25 (Bank Holiday)

**Character:**
London's long May weekend — the unofficial start of summer. Beer
gardens reach peak capacity. The Spring Bank Holiday (Monday) means
another mid-trip closure day for some venues. Chelsea Flower Show
falls the week before, leaving the city in a garden-party mood.
Street markets are at full swing. This is the most "normal" of
London's windows — relatively stable hours, good weather, busy but
manageable crowds. Lower conditional wrong-info risk than Easter or
Christmas, but bank holiday Monday closures remain a quiet trap.

**Conditional wrong info traps:**
1. *Hours trap* — a source shows Monday hours that don't account for
   the Bank Holiday; venue opens 2 hours later or not at all.
2. *Access trap* — a normally walk-in pub or café operates table
   service only during the busy weekend; source says no booking needed.
3. *Character trap* — a source describes a rooftop bar as "often quiet
   on weekday evenings" when the bank holiday weekend makes every evening
   feel like Saturday.

**Planning implications:**
- Smoothest window for general planning
- Monday Bank Holiday is the main trap day
- Currently used as the default task_dates window in city_config

---

### Window 3 — Summer: Notting Hill Carnival
**Dates:** 2026-08-20 (Thu) to 2026-08-26 (Wed)
**Anchor events:**
- Notting Hill Carnival Sunday 2026-08-23
- Notting Hill Carnival Monday 2026-08-24 (Bank Holiday)

**Character:**
Europe's largest street festival transforms an entire London district
for two days. The Notting Hill district is effectively impassable
on Sunday and Monday — roads closed, venues either shut or operating
invite-only events. Sound systems are audible across West London.
Outside Notting Hill, the rest of the city runs normally and is
notably quieter than usual (many Londoners leave for the weekend).
Rooftop bars at peak. An agent planning a Notting Hill venue on
Carnival Sunday or Monday without checking the official site is making
a serious scheduling error. The rest of the week is warm, busy, and
outdoor-friendly.

**Conditional wrong info traps:**
1. *Hours trap* — a source (blog or Yelp) written in February shows
   normal Sunday/Monday hours for a Notting Hill venue that is actually
   closed for carnival weekend.
2. *Access trap* — a source says a Notting Hill restaurant accepts
   walk-ins; during carnival it operates a ticketed event only, booked
   months in advance.
3. *Character trap* — a source describes the Notting Hill area as a
   "quiet, upmarket neighbourhood" — accurate in February, completely
   misleading the weekend of 23-24 August.

**Planning implications:**
- Notting Hill district is a no-go zone Sun 23 + Mon 24
- Rest of London unaffected and enjoyable
- High booking pressure citywide for accommodation

---

### Window 4 — Winter: Christmas Week
**Dates:** 2026-12-23 (Wed) to 2026-12-29 (Tue)
**Anchor events:**
- Christmas Eve 2026-12-24
- Christmas Day 2026-12-25 (Bank Holiday)
- Boxing Day 2026-12-26 (Bank Holiday)
- Substitute Bank Holiday 2026-12-28

**Character:**
Three bank holidays cluster within six days, making this the most
operationally complex window of the year. Most independent venues close
25-26 Dec; many also close 28 Dec. Restaurants that do open run fixed
Christmas menus requiring booking weeks or months ahead. Major tourist
attractions (London Eye, Tower of London) remain open with premium
pricing and mandatory pre-booking. Pubs are exceptionally busy on
Christmas Eve and between Christmas and New Year. Oxford Street and
Covent Garden are packed with post-Christmas sales crowds from 27 Dec.
An agent using normal operating hours for any day in this window
without checking official sources will produce an unworkable schedule.

**Conditional wrong info traps:**
1. *Hours trap* — a Yelp listing shows standard Wednesday–Tuesday
   hours; the venue is closed 25, 26, and 28 Dec with no mention of
   this in the source.
2. *Access trap* — a source says a restaurant is "always bookable
   last-minute"; during Christmas week it has been fully booked since
   October with a fixed menu only.
3. *Character trap* — a source describes a museum as "calm and
   uncrowded on weekday afternoons" — true in October, completely
   false during the post-Christmas sales period (27-29 Dec).

**Planning implications:**
- Safest days: 23 Dec (Wed) and 27, 29 Dec
- High-risk days for closures: 25, 26, 28 Dec
- Booking pressure: restaurants require 4-8 weeks advance notice

---

## HOKKAIDO, Japan
*Season-dependent pool: separate venue selection per window*
*Hokkaido is a region, not a single city — anchor around Sapporo for
urban venues, with excursions to Niseko (ski), Furano/Biei (lavender/flowers)*

### Venue pool strategy
Winter and Summer pools are almost entirely distinct — different
geography, different venue types, different planning logic.
Spring and Autumn are transitional and share some venues with adjacent
seasons. After generation, venues appearing in multiple pools are
flagged with their active window list.

**Winter pool character:** Ski resorts (Niseko, Furano, Rusutsu),
onsen ryokans, mountain lodge restaurants, après-ski bars, Sapporo
urban (ramen bars, izakayas, beer museum, Odori Park winter festival).

**Summer pool character:** Lavender farms (Farm Tomita), flower
fields (Shikisai-no-Oka), Furano wine estate, roadside produce
stands, Sapporo beer garden, outdoor seafood markets, cycling routes.

**Spring pool character:** Snow melt, early flowers, Sapporo city
venues reopening, some ski resorts still operating at higher elevation.
Overlaps partially with both winter (Sapporo urban) and summer (flowers).

**Autumn pool character:** Fall foliage (Daisetsuzan), harvest produce
markets, Sapporo urban, seafood season peak (crab, salmon, sea urchin),
wine harvest at Furano. Shares most urban venues with summer pool.

---

### Window 1 — Winter: Sapporo Snow Festival Week
**Dates:** 2026-02-05 (Thu) to 2026-02-11 (Wed)
**Anchor events:**
- Sapporo Snow Festival 2026-02-05 to 2026-02-11 (approximate — festival
  dates shift slightly year to year; 2026 dates TBC, historically first
  or second week of February)
- National Foundation Day 2026-02-11 (Japanese National Holiday)

**Character:**
Sapporo hosts one of the world's largest winter festivals — enormous
snow and ice sculptures fill Odori Park and Susukino for a full week,
drawing 2 million visitors. The city is at absolute capacity: hotels
booked a year ahead, restaurants packed every night, the underground
shopping network (Odori–Susukino) surging with foot traffic. Ski
resorts (Niseko, Furano, Rusutsu) are at peak season simultaneously —
powder conditions at their best, lift queues long. Onsen ryokans in
the mountains require multi-month advance booking. An agent planning
this window without checking official sites for any venue will likely
find it fully booked or running festival-only access.

**Conditional wrong info traps:**
1. *Hours trap* — a source written in October shows normal winter
   ryokan availability; during Snow Festival week every room is sold
   out and the check-in procedure is different (early arrival required).
2. *Access trap* — a source says an Odori Park area restaurant accepts
   walk-ins; during festival week it operates fixed seatings only,
   booked in advance.
3. *Character trap* — a source describes Sapporo's Susukino district as
   "quiet after midnight"; during Snow Festival week the ice sculpture
   site is illuminated and visited around the clock.

**Venue pool:** Winter (Sapporo urban + ski resort excursions)

---

### Window 2 — Summer: Lavender Peak
**Dates:** 2026-07-16 (Thu) to 2026-07-22 (Wed)
**Anchor events:**
- Furano Lavender Festival (runs throughout July, peak mid-July)
- Marine Day 2026-07-20 (Japanese National Holiday)

**Character:**
The Furano–Biei region turns purple. Farm Tomita is the iconic
lavender farm — free entry but requiring early arrival (before 9am)
to avoid hours-long queues and limited parking. The surrounding
countryside is studded with flower fields of multiple varieties.
Roadside farm stands sell fresh produce, lavender products, and
soft-serve ice cream with legendary local queues. Sapporo itself
runs its famous Beer Garden (outdoor season). The patchwork quilt
hills of Biei are best photographed in morning light. An agent
scheduling a Furano visit after noon on a weekend without checking
parking and queue information is heading for a frustrating day.

**Conditional wrong info traps:**
1. *Hours trap* — a source shows Farm Tomita opening at 8:30am;
   during peak lavender season it opens at 7:00am and queues form
   before that.
2. *Access trap* — a source says "no booking required" for a Furano
   restaurant popular with lavender tourists; during peak July it
   requires same-day reservation by phone only, not walkable.
3. *Character trap* — a source describes the Biei hills as "peaceful
   and uncrowded in the afternoon" — accurate in October, completely
   false during the lavender festival peak.

**Venue pool:** Summer (Furano/Biei farms + Sapporo summer venues;
distinct from winter pool)

---

## RIO DE JANEIRO, Brazil
*Season-dependent pool: Carnival window is partially distinct*
*Rio's seasonality is event-driven more than temperature-driven —
the city is warm year-round, but Carnival restructures everything*

### Venue pool strategy
Three of Rio's four windows share a largely common pool — the
city's beaches, samba bars, botequins, churrascarias, and viewpoints
operate year-round. The Carnival window is a partial exception:
some venues become festival-specific (blocos stages, samba schools
open to public, temporary street food), while others close or
become inaccessible for days. The base pool is flagged with
`available_windows` where a venue is closed or transformed during
Carnival.

---

### Window 1 — Carnival: The Five Days
**Dates:** 2026-02-12 (Thu) to 2026-02-18 (Wed)
**Anchor events:**
- Carnival Thursday (pre-Carnival blocos begin) 2026-02-12
- Carnival Friday 2026-02-13
- Carnival Saturday–Tuesday 2026-02-14–17 (main days)
- Ash Wednesday 2026-02-18 (Carnival ends, city recovers)

**Character:**
Rio Carnival 2026 is among the largest on Earth. The Sambadrome
runs competitive samba school parades Sat-Tue night (tickets sell
out 6+ months ahead). Hundreds of blocos (street parties) take over
neighbourhoods — Ipanema, Santa Teresa, Lapa, and Centro each host
their own character of bloco. Normal city function partially suspends:
many businesses close Mon-Tue, public transport runs carnival-special
routes, and Lapa's famous nightlife district becomes a continuous
outdoor party. Planning a "normal" itinerary during Carnival without
acknowledging the festival is a fundamental misread of the city.
An agent booking a quiet neighbourhood restaurant that is in the path
of a bloco route will find it closed or inaccessible.

**Conditional wrong info traps:**
1. *Hours trap* — a source shows a Santa Teresa restaurant as open
   Tuesday 12-22; during Carnival Tuesday it closes entirely because
   the owner is parading in a samba school.
2. *Access trap* — a source says Sambadrome tickets are available
   at the gate; for Carnival 2026 all sectors are sold out months
   ahead and only secondary market tickets exist at 5-10x face value.
3. *Character trap* — a source describes Lapa as "lively on weekends
   but manageable"; during Carnival week Lapa is continuous day-and-night
   street party with capacity crowds that make venue access impossible
   without advance planning.

**Venue pool:** Carnival (base pool + samba school venues + bloco
gathering points; some standard venues flagged as closed/inaccessible)

---

### Window 2 — Festas Juninas Season
**Dates:** 2026-06-11 (Thu) to 2026-06-17 (Wed)
**Anchor events:**
- Festas Juninas peak (June throughout Brazil — forró, quadrilha,
  corn-based foods; Rio celebrates with neighbourhood street parties)
- Corpus Christi 2026-06-04 (National Holiday — one week before window,
  sets festive mood)

**Character:**
Brazilian winter is Rio's "best" season for visitors — 20-25°C,
low humidity, clear skies, minimal rain. Festas Juninas bring
temporary forró dance stages, corn cake stalls, and quadrilha
performances to neighbourhood squares, particularly in the North
Zone (Tijuca, Madureira) where the tradition is strongest. Bars
and restaurants in Lapa and Santa Teresa run special Festa Junina
menus. This is peak international tourist season for comfort-seekers —
beaches are cooler but still swimmable, queues at Cristo and Sugarloaf
are longer than March but shorter than December. For an agent, the
main challenge is that the Festas Juninas events are neighbourhood-
specific and not well-documented online — the best ones are found via
local knowledge, not Yelp.

**Conditional wrong info traps:**
1. *Hours trap* — a source shows a Lapa samba club as open Thursday
   from 22:00; during Festas Juninas week it opens at 20:00 for a
   special forró night not listed on Yelp.
2. *Access trap* — a source says a viewpoint restaurant in Santa
   Teresa has "reliable walk-in availability on weeknights"; in June
   peak tourist season it is often fully booked by 18:00.
3. *Character trap* — a source describes Madureira market as "a local
   neighbourhood market, not a tourist attraction"; during Festas
   Juninas it hosts one of Rio's largest public celebrations, drawing
   crowds from across the city.

**Venue pool:** Base pool (standard Rio venues; full access)

---

### Window 3 — Summer Peak: January Heat
**Dates:** 2026-01-08 (Thu) to 2026-01-14 (Wed)
**Anchor events:**
- Post-New Year summer peak (Copacabana NYE draws 2M+ people;
  the week after is when beach culture hits maximum intensity)
- No national holidays — chosen for summer character, not events

**Character:**
January is Rio's hottest, most humid month and its highest tourist
season outside Carnival. Copacabana and Ipanema beaches are at
absolute maximum capacity on weekends. The famous kiosks (quiosques)
operate extended hours. Caipirinhas flow. Outdoor dining is the
default. The city is loud, hot, and vibrant. But the extreme heat
(35-40°C with humidity) affects planning: heavy museum visits in
the afternoon are uncomfortable, outdoor queues are punishing, and
the famous cable car to Sugarloaf can have 2-3 hour waits.
Neighbourhood botequins in shaded streets are more appealing than
exposed viewpoints. An agent who doesn't account for the heat and
crowd loading of January will produce an exhausting schedule.

**Conditional wrong info traps:**
1. *Hours trap* — a source written in June (winter, quieter) shows
   Sugarloaf cable car as "typically 30-minute wait"; in January
   the wait is 2-3 hours and pre-booking online is the only way to
   avoid it.
2. *Access trap* — a source says a rooftop bar in Ipanema is "easy
   to walk into on weekday evenings"; in January peak season it
   operates a cover charge and reservation system not mentioned in
   the source.
3. *Character trap* — a source describes an indoor cultural centre in
   Flamengo as "a quiet retreat from the city"; in January it
   becomes one of the few air-conditioned refuges and is often
   at capacity by midday.

**Venue pool:** Base pool (standard Rio venues; full access)

---

## Venue Pool Architecture — Cross-Window Matching

### Travel matrix and seasonality
Physical distance between two venue coordinates is fixed regardless of
season or date. The travel matrix is therefore **season-independent**
and computed once per city after all venues are placed.

For Hokkaido (separate venue pools per window), one matrix is computed
per distinct seasonal pool — not because distances change, but because
the venue sets themselves are different (Niseko ski lodge ≠ Farm Tomita).

Seasonal effects on travel are handled at the **venue access layer**:
- Road closures during Carnival → venue hours_overrides flag the venue
  as inaccessible, not the matrix
- Reduced Christmas bus frequency → irrelevant at the precision of
  "~15 minutes transit" used in planning
- Golden Week Hokkaido bus congestion → handled as an access trap in
  source docs, not a matrix value change

The matrix answers: "how long to get from A to B under normal conditions."
The planning agent applies context (is this route accessible today?)
separately, using official site and hours data.

---

### How matching works
After PLAN_VENUES runs for each distinct seasonal pool, venues are
matched across pools by name similarity (same algorithm as Overpass
matching). A venue appearing in two or more pools is identified as
a **cross-window venue**.

### What the generation agent receives for cross-window venues
The assignment block includes:

```
Seasonal windows: this venue operates across multiple windows.
  Active in: [winter, spring] / [all windows] / [summer, autumn]
  
  Winter character: ski resort clientele, lodge atmosphere, 
    hearty warming food, après-ski hours (late opening)
  Summer character: tourist day-trippers, lighter menu,
    earlier closing time, outdoor terrace open

Generate ground truth that reflects the COMMON operating baseline.
Note window-specific variations in source docs:
  - A summer blog may describe the terrace as a highlight
  - A winter forum post may reference the lodge atmosphere
  Both are valid — they reflect the same venue at different times.
```

### Venue-only windows
For venues that only exist in one seasonal pool (ski resort lodge,
lavender farm stand, Carnival samba school) the assignment block
notes:

```
Seasonal window: WINTER ONLY
This venue does not operate in other seasons.
Source docs should reflect winter operation exclusively.
A blog written in summer should describe the venue as closed/
inaccessible, not as having different hours.
```

---


---

## Summary Table

| City | Windows | Pool strategy |
|------|---------|---------------|
| London | 4 (Easter, Late Spring, Carnival, Christmas) | Single base pool, hours/events vary |
| Hokkaido | 2 (Snow Festival/Winter, Lavender/Summer) | Separate pools; cross-match Sapporo urban venues |
| Rio de Janeiro | 3 (Carnival, Festas Juninas, Summer Peak) | Base pool + Carnival partial override |

---
*Document status: design phase — not yet implemented*
*Next step: integrate seasonal_windows into city_config schema and update PLAN_VENUES to receive window context*
