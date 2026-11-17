# Melvor Idle 2 — how base values become real time, XP and output

Written for the Early Access release (Godot 4.7.2 build, save version 31).
"Verified" marks formulas this project is confident of; anything assumed or simplified says so.
Stat names below match `stat` in `bonus_sources.json` / `active_bonuses.json`.

## Shared rules (verified)

- **XP per completion** = `(BaseExperience + FlatXPGained) × (1 + PercentXPGained)`.
  Global `GlobalPercentXpGained` / `GlobalFlatXpGained` feed the same two terms.
- **Doubling**: one roll of `DoublingChance` per product; success doubles that product's quantity.
- **Preservation**: one roll of `PreservationChance` per completion; success keeps all ingredients.
- Intervals are in milliseconds and floored after modifiers.

## Artisan skills — Smithing, Crafting, Fletching, Runecrafting, Beastcrafting (verified)

- Interval per step = `floor(BaseInterval × (1 + PercentInterval) + FlatInterval)`.
- Steps = `max(1, Steps + FlatSteps)`. The timer fires once per interval and performs **one step**;
  ingredients are spent, products granted and XP awarded only when the last step finishes.
- **Time per craft = interval × steps.** Example: Stone Sword is 2000 ms × 2 steps = 4 s for 6 XP.
- `SkipStepChance`: each fire (while more than one step remains) may complete 2 steps instead of 1.
- Product quantity = `max(1, int((Quantity + FlatProductQuantity) × (1 + PercentProductQuantity)))`,
  each product rolled against its own `Chance`.

## Cooking (verified)

- No steps: one attempt per interval. Interval = `floor(BaseInterval × (1 + PercentInterval) + FlatInterval)`.
- Success chance = `clamp((69 + level + FlatProficiency) / (69 + Difficulty + FlatDifficulty), 0, 1)`.
- A failed attempt still consumes ingredients (unless preserved, incl. `PreservationChanceOnFail`)
  and gives **no XP and no product**. Effective XP/hour = base × success chance.

## Firemaking (verified rewards; interval formula assumed same as artisan)

- One burn per interval. Each product rolls its own `Chance`; currency rewards (e.g. Prayer Points) are always given.

## Woodcutting (verified)

- Interval per hit = `floor((BaseInterval + FlatInterval) × (1 + PercentInterval))` — note flat is added *before* the percent, unlike artisan.
- Tree health = `ceil((BaseMaxHealth + FlatMaxHealth) × (1 + PercentMaxHealth))`; each hit does 1 damage.
- **Time per log = interval × health.** The log and XP are granted when health reaches 0.
- Extra damage: `DoubleDamageChance` (×2), `WeakSpotChance` (3 + 10% of max health), `InstantCutChance` (fells the tree).
- `RandomProductsPerHit` roll every hit; `RandomProductsPerCut` roll once per tree.

## Mining (verified)

- Interval per hit = `floor((BaseInterval + FlatInterval) × (1 + PercentInterval))`.
- Break chance `p = clamp(BaseProductChance × (1 + PercentIncreaseRockBreakChance), 0, 1)`.
- Each hit breaks the rock with chance `p`; otherwise progress rises by `p` and the rock is guaranteed to
  break once progress reaches 1. Progress restarts at `FlatMinimumProspecting` after a break.
- Expected hits per ore (starting from 0) = `(1 − (1 − p)^K) / p` with `K = ceil(1 / p)`.
  Example: Copper Ore, p = 0.3 → 2.53 hits × 1500 ms ≈ 3.8 s per ore.
- Ore and XP are granted on break. `ShatterChance` triples the ore and doubles XP;
  `AdditionalProductQuantityChance` adds 1; `BarItemChance` can also give the bar.

## Fishing (verified loop; interval modifiers not yet confirmed)

- Each catch waits a uniform random time between the min and max interval (table uses the midpoint).
- XP is granted on **every** catch, including junk.
- Fish chance = `clamp((FishChance + FlatFishChance) × (1 + IncreasedFishChance), 0, 1)`; otherwise junk
  (unless `JunkRerollChance` succeeds). The fish is then picked from the weighted table.

## Thieving (verified)

- Interval = `floor((BaseInterval + FlatInterval) × (1 + PercentInterval))`; stun = `floor((2000 + FlatStunInterval) × (1 + PercentStunInterval))`.
- Stealth = `floor((level + FlatStealth) × (1 + PercentStealth))`, perception likewise from the recipe.
  Success = `min(1, (100 + stealth) / (100 + perception))`.
- Failure: damage, then a stun unless `StunAvoidanceChance`. Expected time per success =
  `interval / s + (1 − s) / s × (1 − avoid) × stun`.
- Each success advances one stage (two with `SkipStageChance`) and rolls currency, common and rare loot.
  XP and the completion table are given when stages exceed `max(1, Stages + FlatStages)`.
- Item quantity: rolled uniformly between min and max, then `floor(q × (1 + Fortune / 100))` with
  Fortune = `FlatFortune` (+ `FlatFortuneUnderSuccess` while success is below 100%), then doubled with `DoublingChance`.
- Gold: min and max become `floor((q + FlatCurrencyReward) × (1 + PercentCurrencyReward))`, rolled with
  `BaseCurrencyLootChance × (1 + PercentCurrencyLootChance)`.
- **Damage and food:** a failure deals a uniform 1..Damage unless `DamageAvoidanceChance`. Your HP is shared with
  combat (10 × Hitpoints level); Thieving auto-eats its own food slot with the combat rules (at 25%, up to full).
  A hit from above the auto-eat point straight to 0 kills you: you lose a toolbelt item and the action stops.
  Food per failure and death rate come from a seeded run of 20,000 failures. The food is an input of the action, so
  the time to make it is in the chain (or it comes from the bank with "use items in my bank").
- A food loop (e.g. Salmon Sushi needs Sushi Base, which only the Cooking Stall drops, and the Cooking Stall eats
  sushi) is priced in a second pass of the chain solver, so its cost is counted rather than dropped.
- The Thieving food's own bonuses apply while it is selected (Salmon Sushi +20 stealth, Trout Sushi +10).
  Other stealth: toolbelt gear (Balaclava +15 from Thug, Thug Chief Ring +25 from Thug Chief, Bandit Gloves +30 from
  Bandit), Thieving Mastery Rank 3 +60, Astrology +10 per node; the chest keys (+500 on their chest) are not
  counted. Gear you have equipped is read from the save; the panel's "extra stealth" box tests the rest.
- Not modelled: `SameAreaPickpocketChance` (extra pickpockets of nearby NPCs), distractions and heists.

## Instant steps in chains

- **Opening an item** takes no time: each one opened gives its fixed drops plus one roll of its drop table
  (`ItemOpeningComponent`). E.g. a Seaweed Clump (3% of Sea Edge catches, 0.7% at Dock Waters) opens into 100 Seaweed,
  so Sushi Base = Grain + Seaweed costs a few seconds of fishing rather than a rare Cooking Stall drop.
- **The Grain Mill** (once bought) turns Wheat into Flour or Grain and Sugarcane into Sugar on its own, so it adds
  no active time; the Wheat or Sugarcane it needs is still gathered and costed.
- Both are only used as steps inside chains; they are never ranked on their own.

## Hunting (verified loop; trap-type and bait details simplified)

- Survey (location `BaseSurveyInterval`) until a track is found: chance `t`, guaranteed on the 3rd try.
- Tracking: required steps uniform in `[min, max]` (4 × max for an animal not yet discovered), one step per
  tracking interval, two with `SkipTrackingStepChance`.
- Trap attempt each trapping interval, success `clamp(base + flat + base × increased)`. On failure, unless
  `TrappingReattemptChance` succeeds, `min` more tracking steps are added.
- Each attempt uses a trap (unless trapless or preserved). Trapless is assumed whenever its requirements are
  met, and its bonuses are applied.
- XP per catch = XP formula × a size/weight roll (assumed 1.0 on average).

## Combat (verified against the fights recorded in the save)

`combat.py` simulates fights with these rules; `python -I calculator.py --check-combat` compares it with your loot history.

**Stats** (each side, recomputed whenever an effect changes them)
- Combat stat = `(base + FlatCombatStatValue) × (1 + PercentCombatStatValue)`, rounded. Level = `base + FlatCombatLevel`.
- Accuracy = `floor(floor((level + 9) × (stance attack bonus + 64)) × (1 + PercentAccuracyRating))`.
  Evasion against a stance uses that stance's style levels (magic: 0.3 × Defence + 0.7 × Magic) and its defence bonus,
  then `× (1 + PercentEvasionRating) + FlatEvasionRating`. Darkness removes 5% of both per point (max 20), offset by light.
- Hit chance = `1 − evasion / (2 × accuracy)` if accuracy ≥ evasion, else `accuracy / (2 × evasion)`.
- Max hit, melee and ranged = `floor(22 + L + (L + 17) × strength bonus / 64)`;
  magic = `floor(spell max hit × (1 + magic damage bonus) × (1 + (L + 1) / 200))`;
  then `floor((base × (1 + PercentMaxHit) + FlatMaxHit) × (1 + triangle))`. Min hit = `1 + FlatMinHit + max hit × MinHitBasedOnMaxHit`.
- Turn interval = `max(250, floor(weapon interval × (1 + PercentAttackInterval) + FlatAttackInterval))`.
- Enemy HP = Hitpoints level × 10. Resistance = `clamp(protection / (power + protection), 0, 0.7) × (1 + triangle)`.
- Modifiers come from: worn gear and set bonuses, the stance, active prayers, combat toolbelt, mastery, astrology,
  the combat triangle (±10% max hit, ±15% resistance) and elemental bonuses (spell or weapon element vs the enemy's).
  A modifier only applies when its style / stance / element / stat context matches.

**A turn**
- Both sides start their turn timer when the fight starts; the attack lands when it fires. Multi-hit attacks add
  each hit's interval, then the next turn starts. Respawn is 3000 ms.
- The attack is rolled from the weapon's (or enemy's) special attacks; the normal attack takes whatever chance is left.
- Each hit: hit roll, damage rolled uniformly between min and max hit (× the attack's percent), crit (× 1.5), then
  `int(max(damage × (1 + dealt%) × (1 + taken%) × (1 − resistance) × (1 − ward left / max ward) − armour, 1))`.
- **Armour** is subtracted from every hit until one hit lands for at least the armour value after reduction; that breaks
  it for the rest of the fight. **Ward** loses 1 point per damaging hit.
- Effects simulated: stun (the turn in progress is lost, +modifiers while stunned), damage over time (incl. bleed),
  turn-counting effects with stat modifiers (Ensnared, Staggered, Haste, Evasive, Guarded...), end-of-turn healing,
  on-death damage. Each can be avoided via `AvoidanceChance`.

**XP per kill**
- Per point of health damage dealt by attacks: the stance's rate per skill (0.4 across its skills, plus 0.133 Hitpoints).
  Damage is capped at the enemy's remaining HP; damage-over-time gives no XP.
- Kill bonus: `rate × (4 × armour + 50 × ward)`.
- Prayer: `sqrt(3 + 0.2 × damage × total point cost of active prayers)` for every damaging hit.
- Slayer (only when the enemy is your current task): `HP / 8 + 4`, plus `0.1 × HP^1.15` Slayer Coins.

**Food, auto-eat and death**
- HP carries over between kills; there is no natural regeneration.
- Healing per food = `max(1, int(base healing × (1 + PercentHealing) + FlatHealing))`, × auto-eat efficiency (base 100%).
- Whenever you take damage and are left at or below `round(threshold × max HP)` (threshold 25% + bonuses) and above 0,
  you eat `max(1, floor((limit × max HP − HP) / healing))` at once (limit 100% + bonuses). `FoodPreservationChance` can
  keep them. A food's own bonuses apply while you have it.
- A hit that takes you from above the threshold straight to 0 kills you; eating cannot prevent it.
- The simulator reports food per hour, deaths per hour (one-shot risk), how long your stock lasts, and how long you
  survive after it runs out (from the auto-eat point) or with no food at all (from full HP).
- In the report, food eaten is an input of the fight, so the time to fish and cook it is included in the chain.

**Auto-slayer**
- Each task enemy is picked uniformly from the master's areas, minus blocked enemies; task length is random between
  `BaseTaskLength` and `BaseTaskLength + 3 × Slayer level` and does not depend on the enemy. So every enemy gets the
  same expected kills, and the long-run rate = total XP per kill across them ÷ total time per kill across them.
- Blocking an enemy removes it from both sums; an enemy you cannot kill would stall auto-slayer.

**Dungeons**
- A dungeon is a fixed list of waves fought in order; each wave picks its enemy by weight and repeats `Count` times.
  When the last enemy dies the dungeon's rewards are granted (item and currency doubling apply) and it starts again.
- Enemies inside a dungeon drop nothing, and kills there are not on a slayer task. The dungeon's darkness, effect and
  passives apply like a combat area's.
- The simulator fights each enemy type on its own and adds the results in run order, so time per run = the sum of
  each kill time (including the 3000 ms respawn). HP carried from one enemy type to the next is not modelled; food use
  and one-shot death risk are per enemy type. The 95% range of a run combines each part's range by its share of the time.
- Unlock by kill count is checked against the save's loot history; other requirements show as unconfirmed.

**Not simulated:** stacking, charging, counting and random-magnitude effects; lifesteal and reflect; modifiers with
conditions or scaling.

## Herblore

- A brew prepares, heats and stirs once, then bottles 10 times. Each phase has its own interval:
  `floor(base × (1 + Percent<Phase>Interval + global %) + Flat<Phase>Interval + global flat)`.
- Each bottling uses one set of ingredients (`max(1, int(qty × (1 + PercentIngredientCost)))`, skipped with
  PreservationChance), gives the recipe's XP and its product (`int(max(1, (qty + flat) × (1 + %)))`, doubled with
  DoublingChance). Per bottle: `(preparing + heating + stirring) / 10 + bottling`.
- Herbs come only from Farming, which is passive: chains count them as free (see "Instant steps in chains").

## Agility

- One action per course path. A lap runs the path's obstacles in order; each attempt waits the obstacle's interval
  `floor((base + FlatInterval) × (1 + PercentInterval))`.
- Success: traps `min(0.95, 0.8 + FlatTrapSuccessChance)`; an obstacle without a technique 60%; otherwise
  `n = max(0, difficulty + FlatObstacleDifficulty − technique level + 1)`, success `0.95^(n/2)` (1 when n = 0).
- A failure deals 1..damage (`max(1, floor(damage × (1 + PercentDamageTaken)))`, skipped with AvoidDamageChance) and
  restarts the lap (paying the course's entry cost again), unless RepeatFailedObstacleChance lets you retry that
  obstacle. After a success the next obstacle may be skipped (SkipObstacleChance).
- Expected cost of a lap (time, failures, damage) from the first obstacle on: for obstacle k with per-attempt cost
  c, success p, repeat r and skip s (s = 0 for the first), `X_k = (1 − s)(c + (1 − p)(1 − r)E_{k−1}) /
  (1 − (1 − s)(1 − p))`, `E_k = E_{k−1} + X_k`.
- XP, completion rewards and technique XP come once per lap. One random obstacle reward is rolled per lap
  (chance `clamp((c + flat) × (1 + increased))`, quantity `max(1, int((q + flat) × (1 + %)))`, maybe doubled);
  the calculator counts it in full (a failure before reaching that obstacle can lose it).
- Damage and food use the Thieving model below, with the failures' average damage and the Agility food slot.
- Simplified: technique levels stay at your current ones (they rise as you run courses), technique bonuses for
  meeting an obstacle's difficulty are not added, and rewards for fast laps (3 courses) are left out.

## Astrology

- Each study waits `floor((base + FlatInterval) × (1 + PercentInterval))`, gives the constellation's XP and
  `(knowledge + flat) × (1 + %)` knowledge, then rolls every event (the global ones and the constellation's own)
  with chance `clamp(chance × (1 + PercentEventChance) + FlatEventChance)`.
- Item events give `base + uniform(0, Astrology level × scaling)` items (average used), doubled with the event's
  ItemDoublingChance. Table events pick one of their events by weight. Bonus events (temporary buffs) are not counted.

## Boosters (the panel's "Boost?" tab)

- A booster is any source whose bonus rows target the skill (or every skill, for global XP and interval bonuses).
- Effect: your current best action in the skill is recomputed with the booster's unconditional bonus rows added;
  the change in XP per ms and in items per ms is shown.
- Time to get one:
  - recipe or pickpocket: the action's full chain time ÷ items per completion;
  - combat drop: simulated ms per kill with the chosen loadout ÷ expected drops per kill;
  - shop: gold ÷ your best gold per hour, plus each item cost (less what is in your bank) × its fastest chain
    time;
  - pet: `ScaleFactor` XP (the average XP per pet) ÷ your best XP per hour in that skill.
- For drops, "90% within" uses `ln(10) / p` tries (a geometric wait), i.e. about 2.3 × the average time.

## Levels and the levelling planner

- XP to reach level L = `floor(Σ_{l=1}^{L-1} floor(l + 300 × 2^(l/7)) / 4)` (level 2 = 83 XP, 99 = 13,034,431).
  Checked against the XP and level of every skill in a real save.
- Every skill is capped at level 99. The save stores only XP; the level is worked out from it with this table.
- Agility techniques have their own curve: XP for level L = `floor((L² − L + 600 × (2^(L/3.25) − 2^(1/3.25)) /
  (2^(1/3.25) − 1)) / 6)`, capped per technique (40).
- `planner.py` steps one level at a time: recompute every action with the current levels, take the best XP/hr
  in the skill (chains included), spend `(XP to next level − current XP) ÷ XP/hr` hours on it, and credit XP
  from the chain's other steps to those skills. Counts shown are `times per hour × hours` for each chain step.
- A focus restricts only the final action, by category/subcategory or by what its outputs are used for:
  equipment (ammo if it has an ammo type), runes (any spell's rune cost), food, toolbelt tools, consumables
  (food, ammo, runes, bait, traps, compost, bones), or "ingredient for X" (an input of any action in X, or a
  trap / bait / compost / bones for Hunting / Fishing / Farming / Prayer). If nothing in the focus is
  unlocked at a level, the best action overall is used for that level and flagged.
- Combat: fights are re-chosen at up to 10 checkpoints. Each one does a quick 40-kill scan of every enemy and
  stance that trains the skill, then a full simulation of the top 3. Fights with a death risk are skipped, and
  XP/hr is held until the next checkpoint.
- Not modelled: mastery and other bonuses earned on the way.

## Not yet derived

Farming, Ranching and the Grain Mill are passive; the calculator lists them separately at base values, and
chains count their produce as free when no active action makes it (herbs). How several bonuses to the *same*
stat combine was not confirmed — the calculator assumes they add together. Not modelled: Firemaking bonfires
(a timed buff), Thieving distractions (bought and placed per location), pets' chance to be found.
