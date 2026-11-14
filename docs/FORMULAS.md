# Melvor Idle 2 Demo — how base values become real time, XP and output

Written for the Demo (Godot 4.7.2 build, save version 28).
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

**Not simulated:** stacking, charging, counting and random-magnitude effects; lifesteal and reflect; modifiers with
conditions or scaling.

## Not yet derived

Farming, Ranching and the Grain Mill are passive; the calculator lists them separately at base values.
Herblore is entirely demo-locked. How several bonuses to the *same* stat combine was not confirmed — the
calculator assumes they add together.
