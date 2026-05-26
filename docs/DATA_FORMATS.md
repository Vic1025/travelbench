# TravelBench — Agreed Data Formats

Last updated: 2026-03-06

---

## 1. Ground Truth Venue Record
File location: `data/ground_truth/{city}/venues.json` (array of these)
**Never exposed to agent. Evaluator-only.**

```json
{
  "venue_id": "par_r01",
  "name": "Brasserie Voltaire",
  "city": "paris",
  "category": "restaurant",
  "location": {
    "lat": 48.8566,
    "lng": 2.3522,
    "district": "Le Marais",
    "address": "12 Rue de Bretagne, 75003 Paris"
  },
  "hours": {
    "mon": ["12:00", "22:30"],
    "tue": ["12:00", "22:30"],
    "wed": ["12:00", "22:30"],
    "thu": ["12:00", "22:30"],
    "fri": ["12:00", "23:00"],
    "sat": ["12:00", "23:00"],
    "sun": null
  },
  "avg_cost_local": 38,
  "lunch_cost_local": 28,
  "dinner_cost_local": 48,
  "price_tier": "mid",
  "recommended_visit_minutes": 90,
  "booking_required": true,
  "ticket_availability": {
    "2025-03-08": "sold_out",
    "2025-03-09": "available"
  },
  "labels": [
    "french", "brasserie", "dinner", "lunch",
    "vegetarian", "romantic", "quiet", "classic"
  ],
  "regulations": {
    "pet_friendly": false,
    "wheelchair_accessible": true,
    "parking_nearby": false,
    "age_restriction": null,
    "dress_code": "smart casual",
    "photography_allowed": true,
    "noise_level": "moderate",
    "reservation_required": true,
    "outside_food_allowed": false,
    "family_friendly": true,
    "food_available": true
  },
  "outdoor_sensitivity": "indoor",
  "recommended_pace": "moderate",
  "traffic_tier": "mid",
  "has_official_site": true,
  "local_cuisine": true,
  "recommended_time_window": null,
  "venue_difficulty_score": null
}
```

**Field reference:**

`category` — `restaurant | cafe | bar | museum | attraction | park | neighbourhood`

`price_tier` — `budget | mid | upscale | fine-dining`. Derived from avg_cost_local but stored explicitly.
Thresholds (approximate, city-adjusted at generation): budget ≤ $15, mid ≤ $35, upscale ≤ $70, fine-dining > $70.

`lunch_cost_local / dinner_cost_local` — food/drink venues only. Null for museums, parks, attractions.

`outdoor_sensitivity` — `indoor | outdoor`.
- `indoor`: unaffected by weather.
- `outdoor`: hard F-score fail if scheduled on rainy or stormy day.

`recommended_pace` — `relaxed | moderate | intense`.
Assigned at generation using category + booking_required + recommended_visit_minutes as signals.
- relaxed: park, neighbourhood walk, casual cafe. No booking, short-to-medium duration.
- moderate: sit-down restaurant, mid-size gallery, covered market.
- intense: ticketed museum, formal fine dining, major attraction with queues.
Used by the pace_relaxed P-score handler (duration-weighted day average).

`traffic_tier` — `high | mid | low`.
Determines total_results count shown in search tool responses and number of source documents generated.
- high: well-known venue, actively maintained listings, errors get corrected quickly in corpus.
- low: hidden gem, sparse online presence, errors may have no correction anywhere.

`has_official_site` — bool. If true, a corresponding official site doc must exist in source_docs.

`local_cuisine` — bool, food/drink venues only. True if this venue serves the city's designated local cuisine
(e.g. French for Paris, Japanese for Tokyo). City config holds the cuisine label name. Used by local_cuisine_preference handler.

`recommended_time_window` — null for most venues. Only set for venues with a strong time-of-day quality signal.
Format: `{"optimal_end": "HH:MM", "reason": "stained glass best in morning light"}`.
Used by time_of_day_quality constraint.

`venue_difficulty_score` — float 0.0–1.0. Null at generation time, computed and filled after the full
source corpus is assembled. Stored in DB. Used for avg_venue_difficulty task metadata computation.

**Labels are free-form.** The generation agent assigns whatever tags are genuinely accurate for the venue.
The following are examples of useful label types — not a fixed allowed list:
- Cuisine (food venues): french, japanese, italian, indian, vietnamese, moroccan, etc.
- Meal slot: breakfast, brunch, lunch, dinner, late-night, snack
- Dietary: vegetarian, vegan, halal, kosher, gluten-free
- Vibe: romantic, casual, trendy, traditional, hidden-gem, tourist-trap, locals-favourite,
  cozy, lively, intimate, quiet, loud, solo-friendly, dog-friendly, family-friendly
- Format: outdoor-seating, rooftop, waterfront, live-music, canal-side, covered-market
- Museum/attraction type: art, contemporary-art, history, science, photography, architecture, interactive
- Park type: garden, urban-park, waterfront, viewpoint
- Scale: large, medium, small, iconic, crowded

Tasks may use any label in rubric constraints (label_required, label_excluded). The generation agent
must ensure any label used in a task rubric actually appears on the relevant venue.

**Regulation visibility rule:**
Every regulation that is false or restricted MUST appear in at least one source document naturally.
The agent should be able to find it if it searches specifically for the venue.

**Occasion reasoning rule (LOCKED):**
Venues do NOT have a suitable_occasions field. The task declares the occasion (anniversary, business,
family, etc.) and the planning agent is expected to deduce venue appropriateness from existing fields:
regulations, noise_level, price_tier, dress_code, labels, age_restriction, family_friendly.
This deduction is what the benchmark tests — pre-computing it on the venue would remove the challenge.
Valid task occasions: `family | date | anniversary | honeymoon | business | birthday | retirement | school_trip`

---

## 2. Source Documents
File location: `data/sources/{city}/{venue_id}.json`
**Searchable by agent via tools. May contain conflicts.**

```json
{
  "venue_id": "par_001",
  "yelp_listing": {
    "name": "Brasserie Voltaire",
    "stars": 4.5,
    "category_tags": ["Brasseries", "French"],
    "label_subset": ["french", "romantic", "dinner"],
    "registered_hours": {
      "mon": ["12:00", "22:30"],
      "fri": ["12:00", "22:00"],
      "sun": null
    },
    "top_review_snippet": "Wonderful classic brasserie. Duck confit was excellent.",
    "official_url": null
  },
  "official_site": {
    "exists": false
  },
  "blog_and_forum_posts": [
    {
      "doc_type": "blog",
      "source": "TasteOfParis.blog",
      "author": "Amélie Rousseau",
      "date": "2023-06-10",
      "title": "My Perfect Paris Weekend",
      "likes": 4200,
      "saves": 890,
      "content": "For dinner, we headed to Brasserie Voltaire...",
      "mentions_venue": true,
      "mentioned_hours": "closes 10:30pm",
      "mentioned_regulations": ["no pets allowed", "smart casual dress"]
    },
    {
      "doc_type": "forum",
      "source": "TravelTalk Forums",
      "author": "paris_regular_2024",
      "date": "2025-01-08",
      "title": "Brasserie Voltaire hours update",
      "likes": 12,
      "saves": 3,
      "content": "FYI they extended Friday/Saturday hours — now close at 11pm.",
      "mentions_venue": true,
      "mentioned_hours": "fri/sat close 11pm",
      "mentioned_regulations": []
    }
  ],
  "_conflict_map": [
    {
      "type": "incorrect_hours",
      "day_affected": "fri",
      "incorrect_value": "22:00",
      "true_value": "23:00",
      "inject_into": ["yelp_listing"],
      "correct_in": ["blog_and_forum_posts"]
    }
  ]
}
```

---

## 3. Task Record
File location: `data/tasks/{task_id}.json`
**Public input shown to agent. Rubric hidden (evaluator only).**

```json
{
  "task_id": "par_medium_001",
  "city": "paris",
  "days": 2,
  "start_date": "2025-03-07",
  "difficulty": "medium",
  "public_input": {
    "query": "Planning a 2-day Paris trip starting this Friday...",
    "user_profile": "I never eat before noon. I'm vegetarian. Daily food budget around $60."
  },
  "rubric": {
    "hard_constraints": [
      {"id": "hc_001", "type": "hours_check", "check_method": "code"},
      {"id": "hc_002", "type": "no_overlap", "check_method": "code"},
      {"id": "hc_003", "type": "travel_time_check", "check_method": "code"},
      {"id": "hc_004", "type": "ticket_availability", "check_method": "code"}
    ],
    "partial_constraints": [
      {
        "id": "pc_001",
        "type": "visit_duration",
        "check_method": "code",
        "description": "Scheduled duration within recommended bounds"
      },
      {
        "id": "pc_002",
        "type": "regulation",
        "check_method": "code",
        "regulation_key": "pet_friendly",
        "triggered_by": "travelling with a dog",
        "description": "All venues must be pet-friendly or flagged as unconfirmed"
      },
      {
        "id": "pc_003",
        "type": "preference",
        "check_method": "code",
        "description": "No meals before noon",
        "params": {"min_meal_start_time": "12:00"}
      },
      {
        "id": "pc_004",
        "type": "preference",
        "check_method": "llm",
        "description": "Prefers local hidden gems over tourist spots",
        "params": {"judge_prompt_hint": "..."}
      }
    ]
  }
}
```

---

## 4. Agent Output Format
What the agent must produce, wrapped in `<final_plan>` tags.
**This is what C-score and F-score parse.**

```json
{
  "task_id": "par_medium_001",
  "city": "paris",
  "days": [
    {
      "day": 1,
      "day_of_week": "friday",
      "activities": [
        {
          "time_start": "12:00",
          "time_end": "13:30",
          "venue_id": "par_005",
          "venue_name": "Le Comptoir du Nord",
          "activity_type": "meal",
          "estimated_cost_local": 22,
          "flags": [
            "pet_policy_unconfirmed — could not verify from available sources, please check before visiting"
          ]
        },
        {
          "time_start": "14:30",
          "time_end": "16:30",
          "venue_id": "par_003",
          "venue_name": "Musée de l'Imaginaire",
          "activity_type": "visit",
          "estimated_cost_local": 18,
          "flags": []
        }
      ]
    }
  ],
  "total_estimated_cost_local": 100,
  "unresolved_flags": [
    "par_005: pet policy could not be confirmed from available sources"
  ],
  "planning_notes": "Verified hours via forums. All food venues offer vegetarian."
}
```

**activity_type enum:** `meal | visit | leisure | shopping`
Transport is IMPLICIT — do not add transport as an activity.

**Travel time rule (LOCKED):**
- Gap between consecutive activities must be ≥ travel time between their venues
- Gap may exceed travel time by at most 30 minutes
- If gap > travel time + 30min, agent should insert a resting/free-time slot at a named place (cafe, hotel, park)
- Leaving a venue before its closing time is fine — no penalty

**Visit duration bounds (LOCKED):**
- Minimum: 50% of recommended_visit_minutes
- Maximum: recommended_visit_minutes + max(60min, 50% of recommended_visit_minutes)
- Example: recommended=120min → valid range = 60min to 180min
- Example: recommended=30min  → valid range = 15min to 60min  (max = 30 + max(60,15) = 90... wait, use 30+60=90)

---

## 5. Evaluation Result Record
File location: `results/evaluations/eval__{task_id}__{model}.json`

```json
{
  "task_id": "par_medium_001",
  "model": "claude-sonnet-4-20250514",
  "difficulty": "medium",
  "timestamp": "2026-03-06T15:00:00",
  "scores": {
    "c_score": 0.85,
    "f_score": 0.70,
    "p_score": 0.60,
    "composite": 0.69
  },
  "tier1_completion": {
    "passed": true,
    "tool_call_issues": [],
    "format_issues": []
  },
  "tier2_feasibility": {
    "passed": false,
    "hard_fail_reason": null,
    "partial_checks": [
      {"id": "pc_001", "type": "visit_duration", "score": 1.0, "detail": "OK"},
      {"id": "pc_002", "type": "regulation", "score": 0.5, "detail": "Partial — agent flagged unconfirmed"}
    ]
  },
  "tier3_personalization": {
    "constraint_results": []
  }
}
```

