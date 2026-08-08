// Single source of truth for the "turn counts are an estimate" disclosure.
//
// Turns are a presentation of the token wallet, not a unit the backend meters:
// one short clarification can cost a couple of thousand tokens while a
// multi-attribute submission evaluation can cost hundreds of thousands. Every
// surface that shows a turn figure has to carry the caveat, or a user reads the
// number as a hard count and is surprised when one reply eats a third of it
// (ABS-452).
//
// SHORT rides next to a live balance where horizontal room is tight (the
// in-conversation strip, the /billing wallet, the /cases/new chip). LONG is the
// full sentence used where there's room to explain (the /pricing top-up cards).

export const TURN_APPROX_SHORT =
  "Counts are approximate — complex questions use more";

export const TURN_APPROX_LONG =
  "Turn counts are approximate — a longer, more complex reply draws more from your balance than a short one.";
