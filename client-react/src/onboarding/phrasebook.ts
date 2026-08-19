// ==========================================================================
// The phrasebook — how a vote type sounds when a person says it
// ==========================================================================
// A vote type is written in the voice of a work order: "Add curb extension",
// "Fix broken sidewalk". Nobody arrives at this map with that sentence in their
// head. They arrive with "I nearly got hit crossing here."
//
// So the wall asks what needs fixing in the visitor's own words, and this module
// owns the translation in ONE direction — label → the sentence a person would
// say — and nothing else. It deliberately does NOT own which sentences a map
// shows: that comes from the map's real vote types (tiles.ts), read from the
// API, so adding a vote type to a map adds its sentence to the wall without
// touching this file and removing one removes it.
//
// Two tiers, in order:
//
//   1. CURATED    Hand-written for every label the shipped presets use — the
//                 three default vote sets the Propose-a-Map picker offers
//                 (Walkways, Bikes, Trees), plus everything the shipped maps
//                 add on top. This is the art; there is no rule that produces
//                 "I wait for the bus in the rain here" from "Add bus shelter".
//   2. DERIVED    Pattern rules over the label's verb, for vote types authored
//                 after this file was written (a custom map's own set) and for
//                 free-form suggestions people have typed. Grammatical, plain,
//                 never wrong — just not as good.
//
// There is deliberately no third tier. An earlier version carried a list of
// sentences that named NO vote type, as a floor under a map whose set was
// entirely custom. Those are gone: every slip on the wall must be a real vote
// type that casts a real vote, so a sentence with nothing behind it has no place
// on it. What a map with no vote types at all gets instead is no wall — see
// tiles.ts.
//
// Rules of the voice, so later additions match:
//   · A NEED, NEVER AN OFFER OF LABOUR. This is the rule the wall lives or dies
//     by, and the one it got wrong first. "I'd put flowers where this bed is
//     bare dirt…" translates a vote type into a JOB THE READER WOULD DO, and a
//     first screen that opens by asking a stranger to volunteer is asking the
//     wrong person for the wrong thing: they came to say what is broken, not to
//     sign up for a shift. "This unused patch of land needs flowers…" is the
//     same vote type as a need, and it is what somebody actually thinks.
//     Anything shaped "I'd <verb> …" is the smell; check it before adding one.
//   · SPOKEN ALOUD, present tense. First person where the need is personal ("My
//     street is too hard to cross…"), plain statement where it is the street's
//     rather than the speaker's ("Cars drive too fast here…"). It used to be
//     first person on every line without exception; that rule is what pushed
//     lines into "I'd …" shapes to satisfy it, so it is now a preference, not a
//     requirement. Never "you": a second-person instruction describes somebody
//     else's street.
//   · A complaint is allowed to be a complaint. The vote it produces is the
//     REMEDY (a + for the fix), which is the whole reason a complaint-shaped
//     opener can drive a support-shaped data model.
//   · It is left OPEN — usually a trailing "…", because the sentence is finished
//     by pointing at the map. A line that is already a complete thought on its
//     own ("There is too much litter in this area") may stop without one, but
//     nothing ever closes with a full stop.
//   · Never more than two lines at a phone's width.
// ==========================================================================

/** Label → the sentence a person would actually say. */
export const CURATED: Record<string, string> = {
  // ── Walking ────────────────────────────────────────────────────────────
  "Improve sidewalk": "I dread walking this stretch…",
  "Fix broken sidewalk": "I trip on this sidewalk every single time…",
  "Widen sidewalk": "I have to step aside to let anyone past here…",
  "Add crosswalk": "I cross here every day, and there's no crosswalk…",
  "Add raised crosswalk": "Drivers don't slow down for me at this crossing…",
  "Add pedestrian bridge": "I can't get across here on foot…",
  "Add pedestrian signal": "I cross this on faith…",
  "Add pedestrian refuge island": "I can't make this crossing in one go…",
  "Add pedestrian plaza": "I'd rather this asphalt belonged to us…",
  "Protected walkway": "There's nothing between me and the traffic here…",
  "Add street lighting": "I don't walk down here after dark…",
  "Add intersection lighting": "I can't see a thing on this corner at night…",
  "Clear sidewalk obstruction": "I have to step into the road to get past…",
  "Widen bike lane": "I can't ride this lane safely — it's too narrow…",
  "Extend crossing time": "The light changes before I'm halfway across…",
  "Add water fountain": "I'd refill a bottle here if I could…",
  "Add public restroom": "I can't find anywhere to go for twenty blocks…",
  "Add bench": "I've nowhere to sit and catch my breath here…",

  // ── Danger, speed, signals ─────────────────────────────────────────────
  "Fix dangerous intersection": "My least favorite intersection in the city is…",
  "Fix signal timing": "This light is timed for cars, not for me…",
  "Daylight this corner": "I can't see what's coming around this corner…",
  "Ban turn on red": "Drivers turn straight into me when I'm crossing…",
  "Lower speed limit": "I watch cars do 45 down this 25 street…",
  "Add traffic calming": "I flinch at how fast cars take this stretch…",
  "Add curb extension": "I step into traffic to see past the parked cars…",
  "Street redesign": "This whole junction needs starting over…",
  "Repave street": "I rattle through potholes all down this street…",
  "Add speed bump": "I need something here to slow the traffic down…",

  // ── Cycling ────────────────────────────────────────────────────────────
  "Add bike lane": "I'd ride this route if it had a lane…",
  "Add another bike lane": "One lane isn't enough for all of us here…",
  "Improve bike lane": "I lose this bike lane halfway along…",
  "Add protected bike lane": "Paint doesn't protect me. I want a real lane here…",
  "Repave bike path": "I get my teeth rattled out on this bike path…",
  "Add sharrow (shared lane markings)": "Drivers here don't know I belong too…",
  "Add bike signal phase": "I get the same green as the turning cars…",
  "Add bike bridge": "I can't ride across here at all…",
  "Add bike greenway": "I could ride this the whole way across the city…",
  "Add bike parking": "I've nowhere to lock up here…",
  "Add secure bike parking": "I won't leave my bike here overnight…",
  "Add bike repair station": "I've walked a flat tire home from here…",
  "Add bike counter": "Nobody believes how many of us ride through here…",
  "Add bike share station": "This is where I always wish there were bikes…",
  "Add Citi Bike station": "This is where I always wish there were bikes…",
  "Add Citibike station": "This is where I always wish there were bikes…",
  "Add e-bike charging point": "I've nowhere to charge around here…",
  "Add e-bike charging": "I've nowhere to charge around here…",
  "Sweep this bike lane clear of glass": "I ride through broken glass all along here…",

  // ── Transit ────────────────────────────────────────────────────────────
  "Add bus lane": "I sit on the bus crawling through traffic here…",
  "Add bus shelter": "I wait for the bus in the rain here…",
  "Improve bus stop": "I wait at a bare pole and we call it a bus stop…",
  "Add subway elevator": "I can only reach this platform by the stairs…",
  "Fix broken elevator": "I've found this elevator out for months…",

  // ── Access ─────────────────────────────────────────────────────────────
  "Improve accessibility": "I couldn't get a wheelchair along here…",
  "Fix curb cut": "This curb cut stops my wheelchair dead…",
  "Add curb cut": "I can't get up onto the sidewalk here…",
  "Add tactile paving": "I can't feel underfoot where this crossing is…",
  "Add audible pedestrian signal": "I can't hear when it's safe to cross here…",
  "Add accessible parking space": "I can't park anywhere near enough to get in…",
  "Elevator for reduced mobility": "I can't get up here without taking the stairs…",
  "Improve school crossing": "This school crossing has nothing protecting it…",
  "Improve bridge crossing": "Crossing this bridge on foot frightens me…",

  // ── Green ──────────────────────────────────────────────────────────────
  "Add tree": "This corner needs a tree…",
  "Plant trees": "This corner needs a tree…",
  "More trees": "I can't find a scrap of shade along here…",
  "Add tree-lined street": "I bake walking down this street in July…",
  "Create green corridor": "I'd have this green the whole way…",
  "Add greenway": "I'd walk this whole way under trees…",
  "De-pave street section": "I can't see what this asphalt does for any of us…",
  "De-pave / restore soil": "I can't see what this asphalt does for any of us…",
  "Add bioswale corridor": "I wade down this street every time it rains…",
  "Add a bioswale": "I watch the water pool here with nowhere to go…",
  "Create a tree pit": "There's nowhere to plant a tree here — the pavement's solid…",
  "Add planter boxes": "I want something living on this dead frontage…",
  "Plant native shrubs": "I've never seen anything grow here but litter…",
  "Create a community garden": "This lot could be feeding the block…",
  "Restore soil": "I couldn't dig into this ground if I tried…",
  "Protect existing tree": "I don't think this tree survives what's coming…",
  "Tree needs pruning": "I duck these branches every time I pass…",
  "Tree needs maintenance": "I've watched this tree being left to die…",
  "Add more roses": "I'd like more roses right here…",

  // ── Public space ───────────────────────────────────────────────────────
  "Better lighting": "I find it far too dark here at night…",
  "Add greenway connection": "Nothing connects these two parks…",

  // ── Tactical: things anyone can go out and do ──────────────────────────
  "Chalk the desire line": "I already cut across here, and so does everyone…",
  "Chalk walk-time wayfinding": "People get lost easily here…",
  "Chalk a ghost crosswalk": "My street is too hard to cross…",
  "Chalk a slow-zone message": "Cars drive too fast here…",
  "Pick up trash along here": "There is too much litter in this area",
  "Run a litter pickup here": "I watch everyone's garbage collect on this spot…",
  "Clear this catch basin": "I've seen this drain choke and the corner flood…",
  "Clear the catch basins along this stretch": "I've found every drain along here blocked…",
  "Shovel this sidewalk stretch": "I've never seen anyone shovel this stretch…",
  "Adopt and tend this tree bed": "A tree bed near me needs love…",
  "Flower-bomb this tree bed": "This unused patch of land needs flowers…",
  "Plant a pollinator bed": "This area needs more flowers…",
  "Install a Little Free Library": "This corner could do with a little library",
  "Start a seed library": "Let's install a seed bank here…",
  "Activate the plaza furniture": "Nothing in this plaza invites me to stay…",
  "Repaint this call box or utility box": "This curb needs a fresh paint job…",
  "Get a hydrant spray cap": "There is nowhere to cool down here in August…",
  "Cut back the overgrowth along here": "I duck the whole way along this stretch…",
  "Water the street trees along here": "These street trees are dying of thirst…",
  "Weed and mulch this median": "This median has disappeared under weeds…",
  "Add footbridge": "I can't get over this on foot…",
  "Add pedestrian bridge crossing": "I can't get over this on foot…",

  // ── Odds and ends from older lists ─────────────────────────────────────
  "Add lane": "I don't have enough road space here…",
  "Highway": "I'd rather the traffic went this way…",
};

const ARTICLED = /^(a|an|the|this|these|my|your|our)\s/i;

/** "bus lane" → "a bus lane"; "an elevator" is left alone. */
function withArticle(noun: string): string {
  if (ARTICLED.test(noun)) return noun;
  return `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
}

/** Trim the location tail an imperative label carries, so "run a litter pickup
 *  here" doesn't become "…here here…". */
function stripPlaceTail(phrase: string): string {
  return phrase.replace(
    /\s+(along here|along this stretch|along this street|here|there)$/i,
    ""
  );
}

/** Verb-led rules for labels this file has never seen — a map's own authored
 *  set, or a free-form suggestion somebody typed into the selector. Plain, but
 *  always a sentence a person could say, and always in the first person: a
 *  derived line stands beside curated ones on the same wall. */
function derive(label: string): string {
  const trimmed = label.trim();
  const [verb, ...rest] = trimmed.split(/\s+/);
  const tail = rest.join(" ");
  const object = tail.toLowerCase();

  switch (verb.toLowerCase()) {
    case "add":
    case "install":
    case "build":
      return `I wish there were ${withArticle(object)} here…`;
    case "fix":
    case "repair":
      return `I've been waiting for someone to fix the ${object} here…`;
    case "improve":
    case "upgrade":
      return `I wish the ${object} here were better…`;
    case "widen":
      return `The ${object} here is far too narrow for me…`;
    case "protect":
      return `I don't want to lose the ${object} here…`;
    case "plant":
      return `I'd love to see ${object} on this spot…`;
    case "create":
    case "make":
      return `I'd like this to be ${withArticle(object)}…`;
    case "remove":
    case "ban":
      return `I want ${tail ? `the ${object}` : "this"} here gone…`;
    case "lower":
    case "reduce":
      return `I think the ${object} here is far too high…`;
    case "run":
    case "organize":
    case "organise":
    case "lead":
    case "host":
    case "start":
    case "chalk":
    case "adopt":
    case "clear":
    case "sweep":
    case "shovel":
    case "water":
    case "repaint":
      // The labels most likely to be read as a chore, so this is the branch the
      // no-offer rule matters most on: "I could run a paint day here…" hands the
      // reader a task on the first screen. Naming the need without naming who
      // does the work keeps the derived line beside the curated ones.
      return `Somebody needs to ${stripPlaceTail(trimmed.toLowerCase())} here…`;
    default:
      // Never invent grammar we can't guarantee: keep the author's own words
      // and let the map supply the rest of the sentence.
      return `I'd say ${trimmed.toLowerCase()}, right here…`;
  }
}

/** The sentence for a vote-type label: curated if we wrote one, derived if not. */
export function openerFor(label: string): string {
  return CURATED[label] ?? derive(label);
}

/** Whether the sentence was hand-written (used only by the tests that guard
 *  coverage of the shipped presets' labels). */
export function isCurated(label: string): boolean {
  return label in CURATED;
}
