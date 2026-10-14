# AlgoDial routing design: skills, skill rule sets and queues

This document is the spec for M4 (queues, strategies, skills). It also defines the demo
contact centre used for seed data and tests. Build the models, the matcher in
`brain/core`, the admin pages and the seed command from it.

## 1. How the big systems do it (evidence)

| System | Where skills live | How a call gets its requirements | Scale |
| --- | --- | --- | --- |
| Genesys Cloud | On the user (ACD skills and, separately, language skills) | The flow's Transfer to ACD action sets queue, priority, preferred agents, ACD skills and language | 0 to 5 stars |
| Cisco UCCX | On the agent (competence) and on the CSQ (required skills plus minimum competence) | The script's Select Resource step picks a CSQ; the agent must have all the CSQ's skills | 1 to 10 |
| Wazo | On the agent (integer weight, usually 0 to 100) | The incall attaches a skill rule set and variables (for example `lang`) | any integer |
| VICIdial | Grade 1 to 10 and rank -9 to 9 per user | The DID sends the call to an in-group; grade routing serves higher grades first | 1 to 10 |

Facts we copy:

- **Language is a hard requirement** (Genesys): "an agent that cannot speak the language of the customer does not receive that call, regardless of ACD skill."
- **All required skills must be present** (Cisco): "if you assign multiple skills to the same CSQ the agent must be skilled in all of them."
- **Requirements loosen as the caller waits** (Wazo rule sets `WT < 60, english > 50` then `english > 0`; Genesys bullseye rings).
- **A missing skill is not level 0** (Wazo): an agent without the skill never matches.
- **Best skilled = highest average level of the required skills, ties go to the longest idle** (Genesys Best Available Skills).
- **Skill changes apply immediately**, even to agents already on queue (Genesys).

## 2. The AlgoDial model

Four ideas, each with one job:

| Idea | Job |
| --- | --- |
| **Queue** | A business service line (Membership, Claims...). Members = who is allowed to take its calls. It is the unit for service level, priority, overflow and reports. |
| **Skill** | A capability with a level 1 to 10. Kinds: `language`, `topic`, `tier`. |
| **Skill rule set** | Ordered steps. Each step lasts until a wait time and lists minimum skill levels. Attached to a queue as its default. |
| **Routing ticket** | Per call: queue, language, priority, extra required skills, and who set them (entry point, AI, transfer). This is our version of Genesys' Transfer to ACD settings. |

Level guide for admins (so levels mean the same thing everywhere):

| Level | Meaning |
| --- | --- |
| 1 to 3 | Trainee: can handle simple cases with help |
| 4 to 6 | Competent: handles normal cases alone |
| 7 to 8 | Experienced: handles hard cases |
| 9 to 10 | Expert: handles anything, coaches others |

### Rule semantics (exact)

- A step is `{until_seconds: int | null, requirements: [{skill, min_level}]}`. `null` means "forever" and must be the last step.
- The active step is the first step with `wait_seconds < until_seconds`. At exactly 30 s a step with `until_seconds: 30` has ended.
- An agent matches a step when they are a **member of the queue**, are **Ready and Idle**, and have **every** required skill at `>= min_level`. A missing skill never matches.
- `$language` in a requirement is replaced by the call's language skill (for example `swahili`).
- **Language floor:** every step must contain a `$language` requirement of at least 4. The admin page rejects rule sets that break this. Language is never dropped; if nobody can speak it, the call overflows instead.
- Among matching agents the queue's selection decides: `longest_idle` (default) or `best_skilled` (highest average level of the step's required skills, ties to longest idle). The other strategies from the queue list (ring_all, round_robin, linear, fewest_calls, random) apply among the matching agents too.
- The brain sets a timer for the next step boundary, so a call is re-checked exactly when its requirements loosen.
- A call that is already ringing an agent is not re-checked until that offer ends.

### Two directions, one matcher

1. **Call looks for agent:** when a call enters a queue, or reaches a new step, find the best matching agent.
2. **Agent looks for call:** when an agent becomes Ready and Idle, look at the waiting calls in all their queues, keep those whose active step the agent matches, and take the one with the **highest priority, then longest wait**.

Both use the same pure function in `brain/core`, so they can never disagree.

### Where a call's requirements come from (in order, later wins)

1. **Entry point:** default queue, language and priority for that number.
2. **AI agent:** detected language and topic, mapped to a queue and topic skill. The AI may only choose from the configured queues and skills. Anything else is rejected and the call goes to General, with the rejected tag logged ("AI decides, rules check").
3. **Transfer:** an agent transferring to a queue can add required skills.

Every change to the ticket is an Event, so reports can show why a call went where it went.

## 3. Demo contact centre (seed data)

A fictional public health-insurance helpline. Callers are members, employers and
healthcare providers (hospitals, pharmacies). Most calls are in Swahili, some in English.

### Skills

| Skill | Kind |
| --- | --- |
| swahili | language |
| english | language |
| membership | topic (registration, cards, dependants) |
| claims | topic (claims, payments, benefits) |
| provider_support | topic (facility verification, provider claims) |
| complaints | topic |
| tier2 | tier (senior handling of escalations) |

### Teams and agents

| Agent | Team | swahili | english | membership | claims | provider_support | complaints | tier2 | Queues |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Juma | Front Desk | 9 | 5 | 8 | 4 | | | | Membership, Claims, General |
| Neema | Front Desk | 9 | 8 | 6 | 7 | | | | Membership, Claims, General |
| Baraka | Front Desk | 7 | 9 | 5 | | | | | Membership, General |
| Rehema (trainee) | Front Desk | 9 | | 3 | | | | | Membership, General |
| Halima | Claims & Providers | 8 | 8 | | 9 | 7 | | | Claims, Providers |
| Peter | Claims & Providers | 5 | 9 | | 7 | 8 | | | Claims, Providers |
| Zawadi | Claims & Providers | 9 | 4 | | 6 | | 8 | 7 | Claims, Escalations |
| Mussa | Claims & Providers | 8 | 7 | | | 6 | 9 | 9 | Escalations, Providers |

Supervisors: Asha (Front Desk), Daudi (Claims & Providers).

### Skill rule sets

| Rule set | Step 1 | Step 2 | Step 3 |
| --- | --- | --- | --- |
| R_MEMBERSHIP | < 30 s: `$language >= 6`, `membership >= 6` | < 90 s: `$language >= 5`, `membership >= 3` | forever: `$language >= 4`, `membership >= 1` |
| R_CLAIMS | < 30 s: `$language >= 6`, `claims >= 7` | < 90 s: `$language >= 5`, `claims >= 4` | forever: `$language >= 4`, `claims >= 1` |
| R_PROVIDERS | < 20 s: `$language >= 5`, `provider_support >= 7` | forever: `$language >= 4`, `provider_support >= 4` | |
| R_ESCALATIONS | forever: `$language >= 6`, `tier2 >= 7`, `complaints >= 6` (never loosens) | | |
| R_GENERAL | forever: `$language >= 4` | | |

### Queues

| Queue | Rule set | Selection | Priority | Ring timeout | Max wait, then | Service level |
| --- | --- | --- | --- | --- | --- | --- |
| Membership | R_MEMBERSHIP | longest_idle | 5 | 15 s | 300 s, then General | 80% in 30 s |
| Claims | R_CLAIMS | longest_idle | 5 | 15 s | 300 s, then voicemail | 80% in 30 s |
| Providers | R_PROVIDERS | longest_idle | 10 | 15 s | 180 s, then Claims | 90% in 20 s |
| Escalations | R_ESCALATIONS | best_skilled | 8 | 20 s | 120 s, then voicemail and a callback task | 80% in 60 s |
| General | R_GENERAL | longest_idle | 3 | 15 s | 600 s, then voicemail | 70% in 60 s |

### Entry points

| Number | Destination | Default language | Priority | If the AI or brain fails |
| --- | --- | --- | --- | --- |
| 800 | AI agent "Msaidizi" (may route to Membership, Claims, Escalations, General) | swahili | queue default | General |
| 801 | Providers queue (provider hotline, no AI) | swahili | 10 | Claims |
| 802 | AI agent "Msaidizi" | english | queue default | General |

## 4. Matcher tests (write these first)

All agents Ready and Idle unless stated. "Idle for" decides longest_idle.

| # | Situation | Expected |
| --- | --- | --- |
| T1 | Swahili call in Claims, wait 0 s. Neema idle 100 s, Halima idle 40 s | Step 1 matches Neema and Halima (Juma, Peter, Zawadi fail). Offer to **Neema** |
| T2 | Same call at 45 s; Neema and Halima busy | Step 2 matches Juma, Peter, Zawadi. Offer to the longest idle of them |
| T3 | English call in Claims, wait 0 s | Step 1 matches Neema, Halima, Peter (Juma english 5, Zawadi english 4 fail) |
| T4 | Swahili call in Claims; only Juma Ready (claims 4) | Not offered at 29 s; **offered at 30 s** (step 2 starts) |
| T5 | Swahili call in Escalations; Zawadi and Mussa Ready | Both match. best_skilled: Zawadi (9+7+8)/3 = 8.0, Mussa (8+9+9)/3 = 8.67. Offer to **Mussa** |
| T6 | Swahili call in Escalations; Zawadi and Mussa busy; Neema Ready | Neema has no tier2: never offered. At 120 s the call overflows to voicemail and a callback task is created |
| T7 | Halima becomes Ready. Waiting: Swahili Claims call at 50 s (priority 5) and Swahili Providers call at 5 s (priority 10) | She matches both (Claims step 2; Providers step 1). She gets the **Providers** call (higher priority) |
| T8 | English call in Membership at 200 s; only Rehema Ready | Rehema has no english skill: never offered, even in the last step |
| T9 | AI sets topic skill `fraud` (not configured) | Rejected; call goes to General; a `routing.ai_tag_rejected` event is logged |
| T10 | Admin saves a rule step without `$language` | Rejected by validation (language floor) |
| T11 | Offered agent does not answer within the ring timeout | Agent becomes Not responding; the call is offered to the next matching agent |
| T12 | Supervisor raises Juma's claims to 7 while a Swahili Claims call waits at 10 s | The change applies at once; Juma now matches step 1 |
