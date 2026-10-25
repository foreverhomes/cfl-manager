// What each number means, in plain words: the glossary at the top of the tab, and the hint on
// each tile label and column header (hover or focus; marked with a dotted underline).
export const GLOSSARY = {
  ecw: [
    "Expected category wins per week",
    "How many categories the team would win in a typical week, " +
      "averaged over every opponent, from its projected lineups. Half of them (6 of 12) is an average team.",
  ],
  strength: [
    "Projected strength",
    "The team's rank when its projected strength in every category is added up (1 = strongest).",
  ],
  skater_games: [
    "Skater games per week",
    "Skater games the team is projected to fill each week by setting its best " +
      "available players every day. Bench players count on nights the regulars sit. More games, more counting stats.",
  ],
  goalie_starts: [
    "Goalie starts per week",
    "Starts the team's goalies are projected to make each week, from how " + "often each one starts for his NHL team.",
  ],
  sd: [
    "vs league average",
    "How far the team's projected weekly total is above (blue) or below (red) the league " +
      "average, in standard deviations (sd). About 1 sd is a clear strength or weakness; 2 is extreme.",
  ],
  proj: ["Proj / wk", "The team's projected total in the category per week. GAA and SV% are averages."],
  rank: [
    "Rank",
    "Projected rank in the category among all teams (1 = best). In League at a glance, the color " +
      "shows the same distance from average as the bars.",
  ],
  actual: [
    "Actual",
    "The team's real head-to-head record in the category this season: wins, losses and ties, one per matchup.",
  ],
  need: [
    "Need",
    "How much one more unit of the category is worth to the team, from the Need weighting setting. " +
      "1.00 is average; dark pills (1.15 and up) are the categories worth targeting.",
  ],
  games: [
    "Games / wk",
    "Games per week the player is projected to spend in the team's lineup, with the best " +
      "lineup set every day. For goalies, projected starts.",
  ],
  value: [
    "Value",
    "The player's projected per-game production in every category, counted equally and compared " +
      "with the players good enough to start in this league. 0 is an average starter; higher is better.",
  ],
  need_value: [
    "Need value",
    "Value with each category weighted by the team's Need. Higher than Value means the " +
      "player is strong where the team needs help.",
  ],
  trade_rank: [
    "Trade rank",
    "The player's rank league-wide by Value (#1 = most valuable). Fewer goalies start, so " +
      "goalies are spaced out on the same scale: the best goalie ranks about #9.",
  ],
  market: [
    "Market",
    "Trade value: how far the player is above the last starter in the league. Bench-level " +
      "players are worth about 0, so a package's values add up without two depth players matching a star.",
  ],
  gp: [
    "GP (this/last)",
    "NHL games played this season and last. With few games, the projection leans on last " +
      "season and the league average.",
  ],
  best: [
    "Best categories",
    "The player's three strongest categories, in standard deviations above an average starter.",
  ],
  marginal: [
    "Swing categories",
    "Weighs the categories where several teams are close to this one: one more goal " +
      "or hit there flips a weekly matchup. Categories it dominates, or trails badly in, count less.",
  ],
  weakness: ["Weakest categories", "Weighs the team's lowest-ranked categories most."],
  even: ["Even", "Every category counts the same."],
} as const satisfies Record<string, readonly [term: string, text: string]>;
