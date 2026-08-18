// ==========================================================================
// The phrasebook — how a vote type sounds when a person says it
// ==========================================================================
// A vote type is written in the voice of a work order: "Add curb extension",
// "Fix broken sidewalk". Nobody arrives at this map with that sentence in their
// head. They arrive with "I nearly got hit crossing here."
//
// So the first screen is not a list of vote types; it is a wall of half-finished
// sentences, and the map is where you finish them. This module owns the
// translation in ONE direction — label → the sentence a person would say — and
// nothing else. It deliberately does NOT own which sentences a map shows: that
// comes from the map's real vote types (tiles.ts), read from the API, so adding
// a vote type to a map adds its sentence to the wall without touching this file
// and removing one removes it.
//
// Three tiers, in order:
//
//   1. CURATED    Hand-written for every label the shipped maps actually use.
//                 This is the art; there is no rule that produces "I wait for
//                 the bus in the rain here" from "Add bus shelter".
//   2. DERIVED    Pattern rules over the label's verb, for vote types authored
//                 after this file was written (a custom map's own set) and for
//                 free-form suggestions people have typed. Grammatical, plain,
//                 never wrong — just not as good.
//   3. GENERIC    Sentences that name no vote type at all, for maps whose set is
//                 entirely custom (and for people whose complaint doesn't fit
//                 any of the boxes). Choosing one commits to no vote type; the
//                 map's default resolves at cast time, and the coach shows what
//                 it resolved to before the vote goes anywhere.
//
// Rules of the voice, so later additions match:
//   · First person, present tense, spoken aloud. "I", "my", "you" — not "users".
//   · A complaint is allowed to be a complaint. The vote it produces is the
//     REMEDY (a + for the fix), which is the whole reason a complaint-shaped
//     opener can drive a support-shaped data model.
//   · It ends in "…" because the user finishes it by pointing at the map.
//   · Never more than two lines at a phone's width.
// ==========================================================================

/** Label → the sentence a person would actually say. */
export const CURATED: Record<string, string> = {
  // ── Walking ────────────────────────────────────────────────────────────
  "Improve sidewalk": "Walking this stretch is miserable…",
  "Fix broken sidewalk": "This whole sidewalk is a trip hazard…",
  "Widen sidewalk": "Two people can't pass each other here…",
  "Add crosswalk": "I cross here every day, and there's no crosswalk…",
  "Add raised crosswalk": "Drivers don't slow down for this crossing…",
  "Add pedestrian bridge": "There's no way across here on foot…",
  "Add pedestrian signal": "You cross this on faith…",
  "Add pedestrian refuge island": "This crossing is too wide to make in one go…",
  "Add pedestrian plaza": "This asphalt should belong to people…",
  "Protected walkway": "There's nothing between me and the traffic here…",
  "Add street lighting": "I don't walk down here after dark…",
  "Add intersection lighting": "This corner is pitch black at night…",
  "Clear sidewalk obstruction": "You have to step into the road to get past…",
  "Widen bike lane": "This lane is too narrow to ride safely…",
  "Extend crossing time": "The light changes before I'm halfway across…",
  "Add water fountain": "I'd refill a bottle here if I could…",
  "Add public restroom": "There's nowhere to go for twenty blocks…",
  "Add bench": "There's nowhere to sit and catch your breath…",

  // ── Danger, speed, signals ─────────────────────────────────────────────
  "Fix dangerous intersection": "My least favorite intersection in the city is…",
  "Fix signal timing": "This light is timed for cars, not for people…",
  "Daylight this corner": "You can't see what's coming around this corner…",
  "Ban turn on red": "Drivers turn straight into people crossing here…",
  "Lower speed limit": "This is a 25 street driven at 45…",
  "Add traffic calming": "Cars take this stretch far too fast…",
  "Add curb extension": "You have to walk into traffic to see past the parked cars…",
  "Street redesign": "This whole junction needs starting over…",
  "Repave street": "This route is full of potholes…",
  "Add speed bump": "Something has to slow the traffic here…",

  // ── Cycling ────────────────────────────────────────────────────────────
  "Add bike lane": "I'd ride this route if it had a lane…",
  "Add another bike lane": "One lane isn't enough for this street…",
  "Improve bike lane": "This bike lane gives up halfway along…",
  "Add protected bike lane": "Paint is not protection. This street needs a real lane…",
  "Repave bike path": "This bike path rattles your teeth out…",
  "Add sharrow (shared lane markings)": "Drivers here don't know bikes belong too…",
  "Add bike signal phase": "Bikes and turning cars get the same green here…",
  "Add bike bridge": "There's no way to ride across here…",
  "Add bike greenway": "This could be a whole route across the city…",
  "Add bike parking": "There is nowhere to lock up…",
  "Add secure bike parking": "I won't leave my bike here overnight…",
  "Add bike repair station": "I've walked a flat tire home from here…",
  "Add bike counter": "Nobody believes how many of us ride through here…",
  "Add bike share station": "This is where I always wish there were bikes…",
  "Add Citi Bike station": "This is where I always wish there were bikes…",
  "Add Citibike station": "This is where I always wish there were bikes…",
  "Add e-bike charging point": "There's nowhere to charge around here…",
  "Add e-bike charging": "There's nowhere to charge around here…",
  "Sweep this bike lane clear of glass": "This lane is carpeted in broken glass…",

  // ── Transit ────────────────────────────────────────────────────────────
  "Add bus lane": "The bus crawls along here in traffic…",
  "Add bus shelter": "I wait for the bus in the rain here…",
  "Improve bus stop": "This bus stop is just a pole in the ground…",
  "Add subway elevator": "There's no way down to this platform but stairs…",
  "Fix broken elevator": "The elevator here has been out for months…",

  // ── Access ─────────────────────────────────────────────────────────────
  "Improve accessibility": "You could not get a wheelchair along here…",
  "Fix curb cut": "This curb stops a wheelchair dead…",
  "Add curb cut": "There's no way up onto the sidewalk here…",
  "Add tactile paving": "There's nothing underfoot to tell you where the crossing is…",
  "Add audible pedestrian signal": "You can't tell when it's safe to cross unless you can see…",
  "Add accessible parking space": "There's nowhere to park close enough to get in…",
  "Elevator for reduced mobility": "Getting up here means stairs or nothing…",
  "Improve school crossing": "Kids cross here twice a day, unprotected…",
  "Improve bridge crossing": "Crossing this bridge on foot is frightening…",

  // ── Green ──────────────────────────────────────────────────────────────
  "Add tree": "This corner is begging for a tree…",
  "Plant trees": "This corner is begging for a tree…",
  "More trees": "There's no shade anywhere along here…",
  "Add tree-lined street": "This street is an oven in July…",
  "Create green corridor": "This could be green the whole way…",
  "Add greenway": "Imagine walking this the whole way under trees…",
  "De-pave street section": "This asphalt is doing nobody any good…",
  "De-pave / restore soil": "This asphalt is doing nobody any good…",
  "Add bioswale corridor": "This street floods every time it rains…",
  "Add a bioswale": "The water has nowhere to go here…",
  "Create a tree pit": "There's room for a tree here, if someone made the hole…",
  "Add planter boxes": "This dead frontage needs something living…",
  "Plant native shrubs": "Nothing grows here but litter…",
  "Create a community garden": "This lot could feed the block…",
  "Restore soil": "The ground here is compacted dead…",
  "Protect existing tree": "This tree won't survive what's happening around it…",
  "Tree needs pruning": "These branches are in the way…",
  "Tree needs maintenance": "This tree is being left to die…",
  "Add more roses": "This place could use more roses…",

  // ── Public space ───────────────────────────────────────────────────────
  "Better lighting": "It's too dark here at night…",
  "Add greenway connection": "This is the missing link between two parks…",

  // ── Tactical: things anyone can go out and do ──────────────────────────
  "Chalk the desire line": "Everyone already cuts across here…",
  "Chalk walk-time wayfinding": "Nobody realizes how close everything is from here…",
  "Chalk a ghost crosswalk": "There should be a crosswalk here — let's draw one…",
  "Chalk the sneckdown outline": "The snow showed how much of this junction cars never touch…",
  "Chalk a slow-zone message": "Drivers need a word from the pavement here…",
  "Chalk hopscotch or a play space": "Kids have nowhere to play on this block…",
  "Run a bike bus": "I'd ride to school with the kids along here…",
  "Run a walking school bus": "I'd walk the kids to school this way…",
  "Run a commuter bike train": "I'd rather not ride this commute alone…",
  "Organize a group ride": "This route is better with company…",
  "Route a group run here": "This is where our run should go…",
  "Lead a walking tour": "There's a story along this street worth telling…",
  "Pick up trash along here": "This stretch is knee-deep in litter…",
  "Run a litter pickup here": "This spot collects everyone's garbage…",
  "Clear this catch basin": "This drain is choked, and the corner floods…",
  "Clear the catch basins along this stretch": "Every drain along here is blocked…",
  "Shovel out this crossing": "This crossing is a snowbank all winter…",
  "Shovel this sidewalk stretch": "Nobody shovels this stretch…",
  "Adopt and tend this tree bed": "This tree bed has nobody looking after it…",
  "Flower-bomb this tree bed": "This tree bed is bare dirt and cigarette ends…",
  "Plant a pollinator bed": "There's nothing here for the bees…",
  "Install a Little Free Library": "This corner wants a little library…",
  "Host a community fridge": "People around here could use a fridge…",
  "Start a seed library": "This is where the block could swap seeds…",
  "Activate the plaza furniture": "This plaza is empty because nothing invites you to stay…",
  "Repaint this call box or utility box": "This box is a grey scar on the corner…",
  "Get a hydrant spray cap": "There's nowhere to cool off around here in August…",
  "Cut back the overgrowth along here": "You have to duck the whole way along…",
  "Water the street trees along here": "These trees are dying of thirst…",
  "Weed and mulch this median": "This median is nothing but weeds…",
  "Add footbridge": "There's no way over this on foot…",
  "Add pedestrian bridge crossing": "There's no way over this on foot…",

  // ── Odds and ends from older lists ─────────────────────────────────────
  "Add lane": "There isn't enough road space here…",
  "Highway": "This is where the traffic should go instead…",
};

/** Sentences that name no vote type. Kept deliberately broad — a map whose
 *  whole vote-type set is custom still gets a wall, and someone whose complaint
 *  doesn't fit any box can still start. */
export const GENERIC: string[] = [
  "My least favorite intersection in the city is…",
  "This route is full of potholes…",
  "The worst part of my commute is…",
  "I'd never let a kid cross here alone…",
  "I go the long way around to avoid…",
  "This street is louder than anywhere I know…",
  "There's something missing on this corner…",
  "This is the best block in the city, and nobody knows…",
  "I've nearly been hit right here…",
  "This is where the city gave up…",
  "One thing I'd change about my street is…",
  "Every time it rains, this floods…",
];

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
 *  always a sentence a person could say. */
function derive(label: string): string {
  const trimmed = label.trim();
  const [verb, ...rest] = trimmed.split(/\s+/);
  const tail = rest.join(" ");
  const object = tail.toLowerCase();

  switch (verb.toLowerCase()) {
    case "add":
    case "install":
    case "build":
      return `There should be ${withArticle(object)} here…`;
    case "fix":
    case "repair":
      return `The ${object} here is broken…`;
    case "improve":
    case "upgrade":
      return `The ${object} here could be so much better…`;
    case "widen":
      return `The ${object} here is far too narrow…`;
    case "protect":
      return `The ${object} here needs protecting…`;
    case "plant":
      return `This spot is asking for ${object}…`;
    case "create":
    case "make":
      return `This could be ${withArticle(object)}…`;
    case "remove":
    case "ban":
      return `${tail ? `The ${object}` : "This"} here has to go…`;
    case "lower":
    case "reduce":
      return `The ${object} here is too high…`;
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
      return `I could ${stripPlaceTail(trimmed.toLowerCase())} here…`;
    default:
      // Never invent grammar we can't guarantee: keep the author's own words
      // and let the map supply the rest of the sentence.
      return `${trimmed}, right here…`;
  }
}

/** The sentence for a vote-type label: curated if we wrote one, derived if not. */
export function openerFor(label: string): string {
  return CURATED[label] ?? derive(label);
}

/** Whether the sentence was hand-written (used only by the tests that guard
 *  coverage of the shipped maps' labels). */
export function isCurated(label: string): boolean {
  return label in CURATED;
}
