import * as Data from "effect/Data"
import * as Effect from "effect/Effect"
import type { SchemaError } from "effect/Schema"
import * as Schema from "effect/Schema"
import type * as HttpClient from "effect/http/HttpClient"
import * as HttpClientError from "effect/http/HttpClientError"
import * as HttpClientRequest from "effect/http/HttpClientRequest"
import * as HttpClientResponse from "effect/http/HttpClientResponse"
// non-recursive definitions
export type Me = { readonly "email": string, readonly "name": string | null, readonly "team_id": string | null, readonly "is_admin": boolean, readonly "status": string, readonly "auth": boolean } & { readonly [x: string]: Schema.Json }
export const Me = Schema.StructWithRest(Schema.Struct({ "email": Schema.String.annotate({ "title": "Email" }), "name": Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Name" }), "team_id": Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Team Id" }), "is_admin": Schema.Boolean.annotate({ "title": "Is Admin" }), "status": Schema.String.annotate({ "title": "Status" }), "auth": Schema.Boolean.annotate({ "title": "Auth" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "Me", "identifier": "Me" })
export type ValidationError = { readonly "loc": ReadonlyArray<string | number>, readonly "msg": string, readonly "type": string, readonly "input"?: Schema.Json, readonly "ctx"?: { readonly [x: string]: Schema.Json } } & { readonly [x: string]: Schema.Json }
export const ValidationError = Schema.StructWithRest(Schema.Struct({ "loc": Schema.Array(Schema.Union([Schema.String, Schema.Number.check(Schema.isInt().annotate({ "expected": "an integer" }))])).annotate({ "title": "Location" }), "msg": Schema.String.annotate({ "title": "Message" }), "type": Schema.String.annotate({ "title": "Error Type" }), "input": Schema.optionalKey(Schema.Json.annotate({ "expected": "JSON value", "title": "Input" })), "ctx": Schema.optionalKey(Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" })).annotate({ "title": "Context" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "ValidationError", "identifier": "ValidationError" })
export type Team = { readonly "id": string, readonly "name": string, readonly "short_name": string | null, readonly "rank": number | null, readonly "record": string | null, readonly "games_back": number | null, readonly "win_pct": number | null, readonly "is_me": number } & { readonly [x: string]: Schema.Json }
export const Team = Schema.StructWithRest(Schema.Struct({ "id": Schema.String.annotate({ "title": "Id" }), "name": Schema.String.annotate({ "title": "Name" }), "short_name": Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Short Name" }), "rank": Schema.Union([Schema.Number.check(Schema.isInt().annotate({ "expected": "an integer" })), Schema.Null]).annotate({ "title": "Rank" }), "record": Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Record" }), "games_back": Schema.Union([Schema.Finite, Schema.Null]).annotate({ "title": "Games Back" }), "win_pct": Schema.Union([Schema.Finite, Schema.Null]).annotate({ "title": "Win Pct" }), "is_me": Schema.Number.annotate({ "title": "Is Me" }).check(Schema.isInt().annotate({ "expected": "an integer" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "Team", "identifier": "Team" })
export type ContractEdit = { readonly "years": number, readonly "salary"?: number | null, readonly "contract_type"?: string, readonly "note"?: string | null } & { readonly [x: string]: Schema.Json }
export const ContractEdit = Schema.StructWithRest(Schema.Struct({ "years": Schema.Number.annotate({ "title": "Years" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "salary": Schema.optionalKey(Schema.Union([Schema.Finite, Schema.Null]).annotate({ "title": "Salary" })), "contract_type": Schema.optionalKey(Schema.String.annotate({ "title": "Contract Type", "default": "STANDARD" })), "note": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Note" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "ContractEdit", "identifier": "ContractEdit" })
export type BirthDateEdit = { readonly "birth_date"?: string | null } & { readonly [x: string]: Schema.Json }
export const BirthDateEdit = Schema.StructWithRest(Schema.Struct({ "birth_date": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Birth Date" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "BirthDateEdit", "identifier": "BirthDateEdit" })
export type PickRef = { readonly "draft_year": number, readonly "round": number, readonly "original_team_id": string } & { readonly [x: string]: Schema.Json }
export const PickRef = Schema.StructWithRest(Schema.Struct({ "draft_year": Schema.Number.annotate({ "title": "Draft Year" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "round": Schema.Number.annotate({ "title": "Round" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "original_team_id": Schema.String.annotate({ "title": "Original Team Id" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "PickRef", "identifier": "PickRef" })
export type RuleChange = { readonly "from_year": number, readonly "value": Schema.Json, readonly "note": string | null, readonly "changed_by": string, readonly "changed_at": string } & { readonly [x: string]: Schema.Json }
export const RuleChange = Schema.StructWithRest(Schema.Struct({ "from_year": Schema.Number.annotate({ "title": "From Year" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "value": Schema.Json.annotate({ "expected": "JSON value", "title": "Value" }), "note": Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Note" }), "changed_by": Schema.String.annotate({ "title": "Changed By" }), "changed_at": Schema.String.annotate({ "title": "Changed At" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "RuleChange", "identifier": "RuleChange" })
export type RuleEdit = { readonly "value": Schema.Json, readonly "from_year": number, readonly "note"?: string | null } & { readonly [x: string]: Schema.Json }
export const RuleEdit = Schema.StructWithRest(Schema.Struct({ "value": Schema.Json.annotate({ "expected": "JSON value", "title": "Value" }), "from_year": Schema.Number.annotate({ "title": "From Year" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "note": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Note" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "RuleEdit", "identifier": "RuleEdit" })
export type TradeRequest = { readonly "team_id"?: string, readonly "give"?: ReadonlyArray<string>, readonly "other_team_id"?: string | null, readonly "get"?: ReadonlyArray<string>, readonly "lineup"?: string, readonly "spread"?: number } & { readonly [x: string]: Schema.Json }
export const TradeRequest = Schema.StructWithRest(Schema.Struct({ "team_id": Schema.optionalKey(Schema.String.annotate({ "title": "Team Id", "default": "me" })), "give": Schema.optionalKey(Schema.Array(Schema.String).annotate({ "title": "Give", "default": [] })), "other_team_id": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Other Team Id" })), "get": Schema.optionalKey(Schema.Array(Schema.String).annotate({ "title": "Get", "default": [] })), "lineup": Schema.optionalKey(Schema.String.annotate({ "title": "Lineup", "default": "best" })), "spread": Schema.optionalKey(Schema.Number.annotate({ "title": "Spread", "default": 1.5 }).check(Schema.isFinite().annotate({ "expected": "a finite number" }))) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "TradeRequest", "identifier": "TradeRequest" })
export type NewUser = { readonly "team_id"?: string | null, readonly "role"?: string, readonly "status"?: string, readonly "email": string } & { readonly [x: string]: Schema.Json }
export const NewUser = Schema.StructWithRest(Schema.Struct({ "team_id": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Team Id" })), "role": Schema.optionalKey(Schema.String.annotate({ "title": "Role", "default": "gm" })), "status": Schema.optionalKey(Schema.String.annotate({ "title": "Status", "default": "active" })), "email": Schema.String.annotate({ "title": "Email" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "NewUser", "identifier": "NewUser" })
export type UserAccess = { readonly "team_id"?: string | null, readonly "role"?: string, readonly "status"?: string } & { readonly [x: string]: Schema.Json }
export const UserAccess = Schema.StructWithRest(Schema.Struct({ "team_id": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Team Id" })), "role": Schema.optionalKey(Schema.String.annotate({ "title": "Role", "default": "gm" })), "status": Schema.optionalKey(Schema.String.annotate({ "title": "Status", "default": "active" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "UserAccess", "identifier": "UserAccess" })
export type HTTPValidationError = { readonly "detail"?: ReadonlyArray<ValidationError> } & { readonly [x: string]: Schema.Json }
export const HTTPValidationError = Schema.StructWithRest(Schema.Struct({ "detail": Schema.optionalKey(Schema.Array(ValidationError).annotate({ "title": "Detail" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "HTTPValidationError", "identifier": "HTTPValidationError" })
export type PickDeal = { readonly "kind"?: string, readonly "team_id": string, readonly "other_team_id": string, readonly "send"?: ReadonlyArray<PickRef>, readonly "receive"?: ReadonlyArray<PickRef>, readonly "note"?: string | null } & { readonly [x: string]: Schema.Json }
export const PickDeal = Schema.StructWithRest(Schema.Struct({ "kind": Schema.optionalKey(Schema.String.annotate({ "title": "Kind", "default": "trade" })), "team_id": Schema.String.annotate({ "title": "Team Id" }), "other_team_id": Schema.String.annotate({ "title": "Other Team Id" }), "send": Schema.optionalKey(Schema.Array(PickRef).annotate({ "title": "Send", "default": [] })), "receive": Schema.optionalKey(Schema.Array(PickRef).annotate({ "title": "Receive", "default": [] })), "note": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Note" })) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "PickDeal", "identifier": "PickDeal" })
export type LeagueRule = { readonly "key": string, readonly "label": string, readonly "kind": string, readonly "cite": string, readonly "default": Schema.Json, readonly "value": Schema.Json, readonly "changes": ReadonlyArray<RuleChange> } & { readonly [x: string]: Schema.Json }
export const LeagueRule = Schema.StructWithRest(Schema.Struct({ "key": Schema.String.annotate({ "title": "Key" }), "label": Schema.String.annotate({ "title": "Label" }), "kind": Schema.String.annotate({ "title": "Kind" }), "cite": Schema.String.annotate({ "title": "Cite" }), "default": Schema.Json.annotate({ "expected": "JSON value", "title": "Default" }), "value": Schema.Json.annotate({ "expected": "JSON value", "title": "Value" }), "changes": Schema.Array(RuleChange).annotate({ "title": "Changes" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "LeagueRule", "identifier": "LeagueRule" })
export type LeagueRules = { readonly "year": number, readonly "league_year": number, readonly "can_edit": boolean, readonly "rules": ReadonlyArray<LeagueRule> } & { readonly [x: string]: Schema.Json }
export const LeagueRules = Schema.StructWithRest(Schema.Struct({ "year": Schema.Number.annotate({ "title": "Year" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "league_year": Schema.Number.annotate({ "title": "League Year" }).check(Schema.isInt().annotate({ "expected": "an integer" })), "can_edit": Schema.Boolean.annotate({ "title": "Can Edit" }), "rules": Schema.Array(LeagueRule).annotate({ "title": "Rules" }) }), [Schema.Record(Schema.String, Schema.Json.annotate({ "expected": "JSON value" }))]).annotate({ "title": "LeagueRules", "identifier": "LeagueRules" })
// schemas
export type AuthConfig200 = Schema.Json
export const AuthConfig200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AuthMe200 = Me
export const AuthMe200 = Me
export type AuthGoogle200 = Schema.Json
export const AuthGoogle200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AuthLogout200 = Schema.Json
export const AuthLogout200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Health200 = Schema.Json
export const Health200 = Schema.Json.annotate({ "expected": "JSON value" })
export type SyncParams = { readonly "resources"?: ReadonlyArray<string> | null }
export const SyncParams = Schema.Struct({ "resources": Schema.optionalKey(Schema.Union([Schema.Array(Schema.String), Schema.Null]).annotate({ "title": "Resources", "description": "Subset of: league, players, rosters, standings, matchups, stats, schedule, match, bios, salaries" })) })
export type Sync200 = Schema.Json
export const Sync200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Sync422 = HTTPValidationError
export const Sync422 = HTTPValidationError
export type PublishStatus200 = Schema.Json
export const PublishStatus200 = Schema.Json.annotate({ "expected": "JSON value" })
export type PublishNowParams = { readonly "force"?: boolean }
export const PublishNowParams = Schema.Struct({ "force": Schema.optionalKey(Schema.Boolean.annotate({ "title": "Force", "description": "Write even if nothing changed", "default": false })) })
export type PublishNow200 = Schema.Json
export const PublishNow200 = Schema.Json.annotate({ "expected": "JSON value" })
export type PublishNow422 = HTTPValidationError
export const PublishNow422 = HTTPValidationError
export type SyncLogParams = { readonly "limit"?: number }
export const SyncLogParams = Schema.Struct({ "limit": Schema.optionalKey(Schema.Number.annotate({ "title": "Limit", "default": 20 }).check(Schema.isInt().annotate({ "expected": "an integer" }))) })
export type SyncLog200 = Schema.Json
export const SyncLog200 = Schema.Json.annotate({ "expected": "JSON value" })
export type SyncLog422 = HTTPValidationError
export const SyncLog422 = HTTPValidationError
export type League200 = Schema.Json
export const League200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Categories200 = Schema.Json
export const Categories200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Teams200 = ReadonlyArray<Team>
export const Teams200 = Schema.Array(Team).annotate({ "title": "Response Teams" })
export type Standings200 = Schema.Json
export const Standings200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TeamRoster200 = Schema.Json
export const TeamRoster200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TeamRoster422 = HTTPValidationError
export const TeamRoster422 = HTTPValidationError
export type TeamOverview200 = Schema.Json
export const TeamOverview200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TeamOverview422 = HTTPValidationError
export const TeamOverview422 = HTTPValidationError
export type TeamContracts200 = Schema.Json
export const TeamContracts200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TeamContracts422 = HTTPValidationError
export const TeamContracts422 = HTTPValidationError
export type MatchupsParams = { readonly "period"?: number | null }
export const MatchupsParams = Schema.Struct({ "period": Schema.optionalKey(Schema.Union([Schema.Number.check(Schema.isInt().annotate({ "expected": "an integer" })), Schema.Null]).annotate({ "title": "Period" })) })
export type Matchups200 = Schema.Json
export const Matchups200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Matchups422 = HTTPValidationError
export const Matchups422 = HTTPValidationError
export type PlayersParams = { readonly "status"?: string, readonly "position"?: string | null, readonly "team_id"?: string | null, readonly "q"?: string | null, readonly "sort"?: string, readonly "min_games"?: number, readonly "limit"?: number, readonly "offset"?: number }
export const PlayersParams = Schema.Struct({ "status": Schema.optionalKey(Schema.String.annotate({ "title": "Status", "description": "all, fa, or rostered", "default": "all" })), "position": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Position", "description": "F, D, G, C, LW or RW" })), "team_id": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Team Id" })), "q": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Q", "description": "Name search" })), "sort": Schema.optionalKey(Schema.String.annotate({ "title": "Sort", "description": "value or adp", "default": "value" })), "min_games": Schema.optionalKey(Schema.Number.annotate({ "title": "Min Games", "default": 0 }).check(Schema.isInt().annotate({ "expected": "an integer" }))), "limit": Schema.optionalKey(Schema.Number.annotate({ "title": "Limit", "default": 50 }).check(Schema.isInt().annotate({ "expected": "an integer" }))), "offset": Schema.optionalKey(Schema.Number.annotate({ "title": "Offset", "default": 0 }).check(Schema.isInt().annotate({ "expected": "an integer" }))) })
export type Players200 = Schema.Json
export const Players200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Players422 = HTTPValidationError
export const Players422 = HTTPValidationError
export type Player200 = Schema.Json
export const Player200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Player422 = HTTPValidationError
export const Player422 = HTTPValidationError
export type PlayerContract200 = Schema.Json
export const PlayerContract200 = Schema.Json.annotate({ "expected": "JSON value" })
export type PlayerContract422 = HTTPValidationError
export const PlayerContract422 = HTTPValidationError
export type EditPlayerContractRequestJson = ContractEdit
export const EditPlayerContractRequestJson = ContractEdit
export type EditPlayerContract200 = Schema.Json
export const EditPlayerContract200 = Schema.Json.annotate({ "expected": "JSON value" })
export type EditPlayerContract422 = HTTPValidationError
export const EditPlayerContract422 = HTTPValidationError
export type RevertPlayerContract200 = Schema.Json
export const RevertPlayerContract200 = Schema.Json.annotate({ "expected": "JSON value" })
export type RevertPlayerContract422 = HTTPValidationError
export const RevertPlayerContract422 = HTTPValidationError
export type SetBirthDateRequestJson = BirthDateEdit
export const SetBirthDateRequestJson = BirthDateEdit
export type SetBirthDate200 = Schema.Json
export const SetBirthDate200 = Schema.Json.annotate({ "expected": "JSON value" })
export type SetBirthDate422 = HTTPValidationError
export const SetBirthDate422 = HTTPValidationError
export type TeamPicks200 = Schema.Json
export const TeamPicks200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TeamPicks422 = HTTPValidationError
export const TeamPicks422 = HTTPValidationError
export type RecordPickDealRequestJson = PickDeal
export const RecordPickDealRequestJson = PickDeal
export type RecordPickDeal200 = Schema.Json
export const RecordPickDeal200 = Schema.Json.annotate({ "expected": "JSON value" })
export type RecordPickDeal422 = HTTPValidationError
export const RecordPickDeal422 = HTTPValidationError
export type UndoPickDeal200 = Schema.Json
export const UndoPickDeal200 = Schema.Json.annotate({ "expected": "JSON value" })
export type UndoPickDeal422 = HTTPValidationError
export const UndoPickDeal422 = HTTPValidationError
export type ChangeLogParams = { readonly "team_id"?: string | null, readonly "kind"?: ReadonlyArray<string>, readonly "limit"?: number }
export const ChangeLogParams = Schema.Struct({ "team_id": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Team Id" })), "kind": Schema.optionalKey(Schema.Array(Schema.String).annotate({ "title": "Kind", "description": "Subset of: added, dropped, traded, salary, contract, pick", "default": [] })), "limit": Schema.optionalKey(Schema.Number.annotate({ "title": "Limit", "default": 200 }).check(Schema.isInt().annotate({ "expected": "an integer" }))) })
export type ChangeLog200 = Schema.Json
export const ChangeLog200 = Schema.Json.annotate({ "expected": "JSON value" })
export type ChangeLog422 = HTTPValidationError
export const ChangeLog422 = HTTPValidationError
export type OffseasonParams = { readonly "year"?: number | null }
export const OffseasonParams = Schema.Struct({ "year": Schema.optionalKey(Schema.Union([Schema.Number.check(Schema.isInt().annotate({ "expected": "an integer" })), Schema.Null]).annotate({ "title": "Year", "description": "The offseason's year: RFA or UFA is decided on July 1 of it. Defaults to the next one, after this season" })) })
export type Offseason200 = Schema.Json
export const Offseason200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Offseason422 = HTTPValidationError
export const Offseason422 = HTTPValidationError
export type RulesParams = { readonly "year"?: number | null }
export const RulesParams = Schema.Struct({ "year": Schema.optionalKey(Schema.Union([Schema.Number.check(Schema.isInt().annotate({ "expected": "an integer" })), Schema.Null]).annotate({ "title": "Year", "description": "The league year. Defaults to the current one" })) })
export type Rules200 = LeagueRules
export const Rules200 = LeagueRules
export type Rules422 = HTTPValidationError
export const Rules422 = HTTPValidationError
export type ChangeRuleRequestJson = RuleEdit
export const ChangeRuleRequestJson = RuleEdit
export type ChangeRule200 = LeagueRules
export const ChangeRule200 = LeagueRules
export type ChangeRule422 = HTTPValidationError
export const ChangeRule422 = HTTPValidationError
export type OverviewParams = { readonly "lineup"?: string }
export const OverviewParams = Schema.Struct({ "lineup": Schema.optionalKey(Schema.String.annotate({ "title": "Lineup", "description": "best = daily best lineups from non-minors/IR players over the rest-of-season NHL schedule; active = only players currently ACTIVE in Fantrax", "default": "best" })) })
export type Overview200 = Schema.Json
export const Overview200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Overview422 = HTTPValidationError
export const Overview422 = HTTPValidationError
export type ProfileParams = { readonly "strategy"?: string, readonly "spread"?: number, readonly "punt"?: string | null, readonly "weights"?: string | null, readonly "lineup"?: string }
export const ProfileParams = Schema.Struct({ "strategy": Schema.optionalKey(Schema.String.annotate({ "title": "Strategy", "description": "marginal (swing categories), weakness (worst categories), or even", "default": "marginal" })), "spread": Schema.optionalKey(Schema.Number.annotate({ "title": "Spread", "description": "marginal strategy only: wider = flatter weights", "default": 1.5 }).check(Schema.isFinite().annotate({ "expected": "a finite number" })).check(Schema.isGreaterThan(0).annotate({ "expected": "a value greater than 0" }))), "punt": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Punt", "description": "Comma-separated categories to ignore, e.g. Hit,PM" })), "weights": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Weights", "description": "Manual category weights, e.g. Hit:0,G:1.5 (overrides strategy)" })), "lineup": Schema.optionalKey(Schema.String.annotate({ "title": "Lineup", "description": "best = daily best lineups from non-minors/IR players over the rest-of-season NHL schedule; active = only players currently ACTIVE in Fantrax", "default": "best" })) })
export type Profile200 = Schema.Json
export const Profile200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Profile422 = HTTPValidationError
export const Profile422 = HTTPValidationError
export type TargetsParams = { readonly "source"?: string | null, readonly "drop"?: string | null, readonly "from_team"?: string | null, readonly "offer"?: string | null, readonly "tolerance"?: number, readonly "position"?: string | null, readonly "limit"?: number, readonly "min_games"?: number, readonly "include_minors"?: boolean, readonly "strategy"?: string, readonly "spread"?: number, readonly "punt"?: string | null, readonly "weights"?: string | null, readonly "lineup"?: string }
export const TargetsParams = Schema.Struct({ "source": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Source", "description": "fa (waivers/free agents) or trade. Defaults to trade when from_team/offer is set, else fa" })), "drop": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Drop", "description": "With source=fa: player(s) on your roster to release for each pickup, comma-separated ids or unique parts of names. Without it, the best drop is found for each" })), "from_team": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "From Team", "description": "Only players on this team: team id or a unique part of its name (implies source=trade)" })), "offer": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Offer", "description": "Player(s) on your roster to trade away, comma-separated ids or unique parts of names. Returns only players of comparable market value (implies source=trade)" })), "tolerance": Schema.optionalKey(Schema.Number.annotate({ "title": "Tolerance", "description": "With offer: how far the trade rank may differ, as a fraction (0.25 = within 25%, at least ±3 ranks)", "default": 0.25 }).check(Schema.isFinite().annotate({ "expected": "a finite number" })).check(Schema.isGreaterThan(0).annotate({ "expected": "a value greater than 0" }))), "position": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Position", "description": "F, D, G, C, LW or RW" })), "limit": Schema.optionalKey(Schema.Number.annotate({ "title": "Limit", "default": 25 }).check(Schema.isInt().annotate({ "expected": "an integer" }))), "min_games": Schema.optionalKey(Schema.Number.annotate({ "title": "Min Games", "description": "Min NHL games (this + last season)", "default": 5 }).check(Schema.isInt().annotate({ "expected": "an integer" }))), "include_minors": Schema.optionalKey(Schema.Boolean.annotate({ "title": "Include Minors", "default": false })), "strategy": Schema.optionalKey(Schema.String.annotate({ "title": "Strategy", "description": "marginal (swing categories), weakness (worst categories), or even", "default": "marginal" })), "spread": Schema.optionalKey(Schema.Number.annotate({ "title": "Spread", "description": "marginal strategy only: wider = flatter weights", "default": 1.5 }).check(Schema.isFinite().annotate({ "expected": "a finite number" })).check(Schema.isGreaterThan(0).annotate({ "expected": "a value greater than 0" }))), "punt": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Punt", "description": "Comma-separated categories to ignore, e.g. Hit,PM" })), "weights": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Weights", "description": "Manual category weights, e.g. Hit:0,G:1.5 (overrides strategy)" })), "lineup": Schema.optionalKey(Schema.String.annotate({ "title": "Lineup", "description": "best = daily best lineups from non-minors/IR players over the rest-of-season NHL schedule; active = only players currently ACTIVE in Fantrax", "default": "best" })) })
export type Targets200 = Schema.Json
export const Targets200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Targets422 = HTTPValidationError
export const Targets422 = HTTPValidationError
export type TradePartnersParams = { readonly "per_team"?: number, readonly "strategy"?: string, readonly "spread"?: number, readonly "punt"?: string | null, readonly "weights"?: string | null, readonly "lineup"?: string }
export const TradePartnersParams = Schema.Struct({ "per_team": Schema.optionalKey(Schema.Number.annotate({ "title": "Per Team", "default": 3 }).check(Schema.isInt().annotate({ "expected": "an integer" }))), "strategy": Schema.optionalKey(Schema.String.annotate({ "title": "Strategy", "description": "marginal (swing categories), weakness (worst categories), or even", "default": "marginal" })), "spread": Schema.optionalKey(Schema.Number.annotate({ "title": "Spread", "description": "marginal strategy only: wider = flatter weights", "default": 1.5 }).check(Schema.isFinite().annotate({ "expected": "a finite number" })).check(Schema.isGreaterThan(0).annotate({ "expected": "a value greater than 0" }))), "punt": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Punt", "description": "Comma-separated categories to ignore, e.g. Hit,PM" })), "weights": Schema.optionalKey(Schema.Union([Schema.String, Schema.Null]).annotate({ "title": "Weights", "description": "Manual category weights, e.g. Hit:0,G:1.5 (overrides strategy)" })), "lineup": Schema.optionalKey(Schema.String.annotate({ "title": "Lineup", "description": "best = daily best lineups from non-minors/IR players over the rest-of-season NHL schedule; active = only players currently ACTIVE in Fantrax", "default": "best" })) })
export type TradePartners200 = Schema.Json
export const TradePartners200 = Schema.Json.annotate({ "expected": "JSON value" })
export type TradePartners422 = HTTPValidationError
export const TradePartners422 = HTTPValidationError
export type TradeRequestJson = TradeRequest
export const TradeRequestJson = TradeRequest
export type Trade200 = Schema.Json
export const Trade200 = Schema.Json.annotate({ "expected": "JSON value" })
export type Trade422 = HTTPValidationError
export const Trade422 = HTTPValidationError
export type AdminUsers200 = Schema.Json
export const AdminUsers200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AdminAddUserRequestJson = NewUser
export const AdminAddUserRequestJson = NewUser
export type AdminAddUser200 = Schema.Json
export const AdminAddUser200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AdminAddUser422 = HTTPValidationError
export const AdminAddUser422 = HTTPValidationError
export type AdminSetAccessRequestJson = UserAccess
export const AdminSetAccessRequestJson = UserAccess
export type AdminSetAccess200 = Schema.Json
export const AdminSetAccess200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AdminSetAccess422 = HTTPValidationError
export const AdminSetAccess422 = HTTPValidationError
export type AdminRemoveUser200 = Schema.Json
export const AdminRemoveUser200 = Schema.Json.annotate({ "expected": "JSON value" })
export type AdminRemoveUser422 = HTTPValidationError
export const AdminRemoveUser422 = HTTPValidationError

export interface OperationConfig {
  /**
   * Whether or not the response should be included in the value returned from
   * an operation.
   *
   * If set to `true`, a tuple of `[A, HttpClientResponse]` will be returned,
   * where `A` is the success type of the operation.
   *
   * If set to `false`, only the success type of the operation will be returned.
   */
  readonly includeResponse?: boolean | undefined
}

/**
 * A utility type which optionally includes the response in the return result
 * of an operation based upon the value of the `includeResponse` configuration
 * option.
 */
export type WithOptionalResponse<A, Config extends OperationConfig | undefined> = Config extends {
  readonly includeResponse: true
} ? [A, HttpClientResponse.HttpClientResponse]
  : Config extends { readonly includeResponse?: false | undefined } | undefined ? A
  : A | [A, HttpClientResponse.HttpClientResponse]

export const make = (
  httpClient: HttpClient.HttpClient,
  options: {
    readonly transformClient?: ((client: HttpClient.HttpClient) => Effect.Effect<HttpClient.HttpClient>) | undefined
  } = {}
): Cfl => {
  const unexpectedStatus = (response: HttpClientResponse.HttpClientResponse) =>
    Effect.flatMap(
      Effect.orElseSucceed(response.json, () => "Unexpected status code"),
      (description) =>
        Effect.fail(
          new HttpClientError.HttpClientError({
            reason: new HttpClientError.StatusCodeError({
              request: response.request,
              response,
              description: typeof description === "string" ? description : JSON.stringify(description),
            }),
          }),
        ),
    )
  const withResponse = <Config extends OperationConfig>(config: Config | undefined) => (
    f: (response: HttpClientResponse.HttpClientResponse) => Effect.Effect<any, any>,
  ): (request: HttpClientRequest.HttpClientRequest) => Effect.Effect<any, any> => {
    const withOptionalResponse = (
      config?.includeResponse
        ? (response: HttpClientResponse.HttpClientResponse) => Effect.map(f(response), (a) => [a, response])
        : (response: HttpClientResponse.HttpClientResponse) => f(response)
    ) as any
    return options?.transformClient
      ? (request) =>
          Effect.flatMap(
            Effect.flatMap(options.transformClient!(httpClient), (client) => client.execute(request)),
            withOptionalResponse
          )
      : (request) => Effect.flatMap(httpClient.execute(request), withOptionalResponse)
  }
  const __encodePathParam = encodeURIComponent
  const __makePathRequest = (
    method: (url: string) => HttpClientRequest.HttpClientRequest,
    parameters: ReadonlyArray<string>,
    getPath: () => string,
  ) => Effect.suspend(() => {
    const fail = (description: string, cause?: unknown) => Effect.fail(
      new HttpClientError.HttpClientError({
        reason: new HttpClientError.InvalidUrlError({
          request: method(""),
          cause,
          description,
        }),
      }),
    )
    if (parameters.some((value) => value === "" || /^(?:\.|%2e){1,2}$/i.test(value))) {
      return fail("Path parameters must be non-empty and cannot be dot segments")
    }
    let path: string
    try {
      path = getPath()
    } catch (cause) {
      return fail("Failed to encode path parameter", cause)
    }
    if (path.split("/").some((segment) => /^(?:\.|%2e){1,2}$/i.test(segment))) {
      return fail("Request paths cannot contain dot segments")
    }
    return Effect.succeed(method(path))
  })
  const decodeSuccess =
    <Schema extends Schema.Constraint>(schema: Schema) =>
    (response: HttpClientResponse.HttpClientResponse) =>
      HttpClientResponse.schemaBodyJson(schema)(response)
  const decodeError =
    <const Tag extends string, Schema extends Schema.Constraint>(tag: Tag, schema: Schema) =>
    (response: HttpClientResponse.HttpClientResponse) =>
      Effect.flatMap(
        HttpClientResponse.schemaBodyJson(schema)(response),
        (cause) => Effect.fail(CflError(tag, cause, response)),
      )
  return {
    httpClient,
    "authConfig": (options: Parameters<Cfl["authConfig"]>[0]) => HttpClientRequest.get("/auth/config").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AuthConfig200),
      orElse: unexpectedStatus
    }))
    ),
    "authMe": (options: Parameters<Cfl["authMe"]>[0]) => HttpClientRequest.get("/auth/me").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AuthMe200),
      orElse: unexpectedStatus
    }))
    ),
    "authGoogle": (options: Parameters<Cfl["authGoogle"]>[0]) => HttpClientRequest.post("/auth/google").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AuthGoogle200),
      orElse: unexpectedStatus
    }))
    ),
    "authLogout": (options: Parameters<Cfl["authLogout"]>[0]) => HttpClientRequest.post("/auth/logout").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AuthLogout200),
      orElse: unexpectedStatus
    }))
    ),
    "health": (options: Parameters<Cfl["health"]>[0]) => HttpClientRequest.get("/health").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Health200),
      orElse: unexpectedStatus
    }))
    ),
    "sync": (options: Parameters<Cfl["sync"]>[0]) => HttpClientRequest.post("/sync").pipe(
      HttpClientRequest.setUrlParams({ "resources": options?.params?.["resources"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Sync200),
      "422": decodeError("Sync422", Sync422),
      orElse: unexpectedStatus
    }))
    ),
    "publishStatus": (options: Parameters<Cfl["publishStatus"]>[0]) => HttpClientRequest.get("/publish").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(PublishStatus200),
      orElse: unexpectedStatus
    }))
    ),
    "publishNow": (options: Parameters<Cfl["publishNow"]>[0]) => HttpClientRequest.post("/publish").pipe(
      HttpClientRequest.setUrlParams({ "force": options?.params?.["force"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(PublishNow200),
      "422": decodeError("PublishNow422", PublishNow422),
      orElse: unexpectedStatus
    }))
    ),
    "syncLog": (options: Parameters<Cfl["syncLog"]>[0]) => HttpClientRequest.get("/sync/log").pipe(
      HttpClientRequest.setUrlParams({ "limit": options?.params?.["limit"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(SyncLog200),
      "422": decodeError("SyncLog422", SyncLog422),
      orElse: unexpectedStatus
    }))
    ),
    "league": (options: Parameters<Cfl["league"]>[0]) => HttpClientRequest.get("/league").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(League200),
      orElse: unexpectedStatus
    }))
    ),
    "categories": (options: Parameters<Cfl["categories"]>[0]) => HttpClientRequest.get("/categories").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Categories200),
      orElse: unexpectedStatus
    }))
    ),
    "teams": (options: Parameters<Cfl["teams"]>[0]) => HttpClientRequest.get("/teams").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Teams200),
      orElse: unexpectedStatus
    }))
    ),
    "standings": (options: Parameters<Cfl["standings"]>[0]) => HttpClientRequest.get("/standings").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Standings200),
      orElse: unexpectedStatus
    }))
    ),
    "teamRoster": (teamId, options: Parameters<Cfl["teamRoster"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/teams/" + __encodePathParam(teamId) + "/roster").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(TeamRoster200),
      "422": decodeError("TeamRoster422", TeamRoster422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "teamOverview": (teamId, options: Parameters<Cfl["teamOverview"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/teams/" + __encodePathParam(teamId) + "/overview").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(TeamOverview200),
      "422": decodeError("TeamOverview422", TeamOverview422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "teamContracts": (teamId, options: Parameters<Cfl["teamContracts"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/teams/" + __encodePathParam(teamId) + "/contracts").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(TeamContracts200),
      "422": decodeError("TeamContracts422", TeamContracts422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "matchups": (options: Parameters<Cfl["matchups"]>[0]) => HttpClientRequest.get("/matchups").pipe(
      HttpClientRequest.setUrlParams({ "period": options?.params?.["period"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Matchups200),
      "422": decodeError("Matchups422", Matchups422),
      orElse: unexpectedStatus
    }))
    ),
    "players": (options: Parameters<Cfl["players"]>[0]) => HttpClientRequest.get("/players").pipe(
      HttpClientRequest.setUrlParams({ "status": options?.params?.["status"] as any, "position": options?.params?.["position"] as any, "team_id": options?.params?.["team_id"] as any, "q": options?.params?.["q"] as any, "sort": options?.params?.["sort"] as any, "min_games": options?.params?.["min_games"] as any, "limit": options?.params?.["limit"] as any, "offset": options?.params?.["offset"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Players200),
      "422": decodeError("Players422", Players422),
      orElse: unexpectedStatus
    }))
    ),
    "player": (fantraxId, options: Parameters<Cfl["player"]>[1]) => __makePathRequest(HttpClientRequest.get, [fantraxId], () => "/players/" + __encodePathParam(fantraxId) + "").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Player200),
      "422": decodeError("Player422", Player422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "playerContract": (fantraxId, options: Parameters<Cfl["playerContract"]>[1]) => __makePathRequest(HttpClientRequest.get, [fantraxId], () => "/players/" + __encodePathParam(fantraxId) + "/contract").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(PlayerContract200),
      "422": decodeError("PlayerContract422", PlayerContract422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "editPlayerContract": (fantraxId, options: Parameters<Cfl["editPlayerContract"]>[1]) => __makePathRequest(HttpClientRequest.put, [fantraxId], () => "/players/" + __encodePathParam(fantraxId) + "/contract").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(EditPlayerContract200),
      "422": decodeError("EditPlayerContract422", EditPlayerContract422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "revertPlayerContract": (fantraxId, options: Parameters<Cfl["revertPlayerContract"]>[1]) => __makePathRequest(HttpClientRequest.delete, [fantraxId], () => "/players/" + __encodePathParam(fantraxId) + "/contract").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(RevertPlayerContract200),
      "422": decodeError("RevertPlayerContract422", RevertPlayerContract422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "setBirthDate": (fantraxId, options: Parameters<Cfl["setBirthDate"]>[1]) => __makePathRequest(HttpClientRequest.put, [fantraxId], () => "/players/" + __encodePathParam(fantraxId) + "/birth-date").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(SetBirthDate200),
      "422": decodeError("SetBirthDate422", SetBirthDate422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "teamPicks": (teamId, options: Parameters<Cfl["teamPicks"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/teams/" + __encodePathParam(teamId) + "/picks").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(TeamPicks200),
      "422": decodeError("TeamPicks422", TeamPicks422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "recordPickDeal": (options: Parameters<Cfl["recordPickDeal"]>[0]) => HttpClientRequest.post("/picks/deals").pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(RecordPickDeal200),
      "422": decodeError("RecordPickDeal422", RecordPickDeal422),
      orElse: unexpectedStatus
    }))
    ),
    "undoPickDeal": (dealId, options: Parameters<Cfl["undoPickDeal"]>[1]) => __makePathRequest(HttpClientRequest.delete, [dealId], () => "/picks/deals/" + __encodePathParam(dealId) + "").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(UndoPickDeal200),
      "422": decodeError("UndoPickDeal422", UndoPickDeal422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "changeLog": (options: Parameters<Cfl["changeLog"]>[0]) => HttpClientRequest.get("/changes").pipe(
      HttpClientRequest.setUrlParams({ "team_id": options?.params?.["team_id"] as any, "kind": options?.params?.["kind"] as any, "limit": options?.params?.["limit"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(ChangeLog200),
      "422": decodeError("ChangeLog422", ChangeLog422),
      orElse: unexpectedStatus
    }))
    ),
    "offseason": (options: Parameters<Cfl["offseason"]>[0]) => HttpClientRequest.get("/offseason").pipe(
      HttpClientRequest.setUrlParams({ "year": options?.params?.["year"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Offseason200),
      "422": decodeError("Offseason422", Offseason422),
      orElse: unexpectedStatus
    }))
    ),
    "rules": (options: Parameters<Cfl["rules"]>[0]) => HttpClientRequest.get("/rules").pipe(
      HttpClientRequest.setUrlParams({ "year": options?.params?.["year"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Rules200),
      "422": decodeError("Rules422", Rules422),
      orElse: unexpectedStatus
    }))
    ),
    "changeRule": (key, options: Parameters<Cfl["changeRule"]>[1]) => __makePathRequest(HttpClientRequest.put, [key], () => "/rules/" + __encodePathParam(key) + "").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(ChangeRule200),
      "422": decodeError("ChangeRule422", ChangeRule422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "overview": (options: Parameters<Cfl["overview"]>[0]) => HttpClientRequest.get("/analysis/overview").pipe(
      HttpClientRequest.setUrlParams({ "lineup": options?.params?.["lineup"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Overview200),
      "422": decodeError("Overview422", Overview422),
      orElse: unexpectedStatus
    }))
    ),
    "profile": (teamId, options: Parameters<Cfl["profile"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/analysis/teams/" + __encodePathParam(teamId) + "/profile").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.setUrlParams({ "strategy": options?.params?.["strategy"] as any, "spread": options?.params?.["spread"] as any, "punt": options?.params?.["punt"] as any, "weights": options?.params?.["weights"] as any, "lineup": options?.params?.["lineup"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Profile200),
      "422": decodeError("Profile422", Profile422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "targets": (teamId, options: Parameters<Cfl["targets"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/analysis/teams/" + __encodePathParam(teamId) + "/targets").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.setUrlParams({ "source": options?.params?.["source"] as any, "drop": options?.params?.["drop"] as any, "from_team": options?.params?.["from_team"] as any, "offer": options?.params?.["offer"] as any, "tolerance": options?.params?.["tolerance"] as any, "position": options?.params?.["position"] as any, "limit": options?.params?.["limit"] as any, "min_games": options?.params?.["min_games"] as any, "include_minors": options?.params?.["include_minors"] as any, "strategy": options?.params?.["strategy"] as any, "spread": options?.params?.["spread"] as any, "punt": options?.params?.["punt"] as any, "weights": options?.params?.["weights"] as any, "lineup": options?.params?.["lineup"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Targets200),
      "422": decodeError("Targets422", Targets422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "tradePartners": (teamId, options: Parameters<Cfl["tradePartners"]>[1]) => __makePathRequest(HttpClientRequest.get, [teamId], () => "/analysis/teams/" + __encodePathParam(teamId) + "/trade-partners").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.setUrlParams({ "per_team": options?.params?.["per_team"] as any, "strategy": options?.params?.["strategy"] as any, "spread": options?.params?.["spread"] as any, "punt": options?.params?.["punt"] as any, "weights": options?.params?.["weights"] as any, "lineup": options?.params?.["lineup"] as any }),
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(TradePartners200),
      "422": decodeError("TradePartners422", TradePartners422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "trade": (options: Parameters<Cfl["trade"]>[0]) => HttpClientRequest.post("/analysis/trade").pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(Trade200),
      "422": decodeError("Trade422", Trade422),
      orElse: unexpectedStatus
    }))
    ),
    "adminUsers": (options: Parameters<Cfl["adminUsers"]>[0]) => HttpClientRequest.get("/admin/users").pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AdminUsers200),
      orElse: unexpectedStatus
    }))
    ),
    "adminAddUser": (options: Parameters<Cfl["adminAddUser"]>[0]) => HttpClientRequest.post("/admin/users").pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AdminAddUser200),
      "422": decodeError("AdminAddUser422", AdminAddUser422),
      orElse: unexpectedStatus
    }))
    ),
    "adminSetAccess": (email, options: Parameters<Cfl["adminSetAccess"]>[1]) => __makePathRequest(HttpClientRequest.put, [email], () => "/admin/users/" + __encodePathParam(email) + "").pipe(
    Effect.flatMap((request) => request.pipe(
      HttpClientRequest.bodyJsonUnsafe(options.payload),
      withResponse(options.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AdminSetAccess200),
      "422": decodeError("AdminSetAccess422", AdminSetAccess422),
      orElse: unexpectedStatus
    }))
    ))
  ),
    "adminRemoveUser": (email, options: Parameters<Cfl["adminRemoveUser"]>[1]) => __makePathRequest(HttpClientRequest.delete, [email], () => "/admin/users/" + __encodePathParam(email) + "").pipe(
    Effect.flatMap((request) => request.pipe(
      withResponse(options?.config)(HttpClientResponse.matchStatus({
      "2xx": decodeSuccess(AdminRemoveUser200),
      "422": decodeError("AdminRemoveUser422", AdminRemoveUser422),
      orElse: unexpectedStatus
    }))
    ))
  )
  }
}

export interface Cfl {
  readonly httpClient: HttpClient.HttpClient
  /**
* What the sign-in page needs: whether logins are on, and the (public) Google client id.
*/
readonly "authConfig": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AuthConfig200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AuthConfig200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Who's signed in, including someone still waiting for approval. 401 when nobody is.
*/
readonly "authMe": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AuthMe200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AuthMe200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Where Google's sign-in page posts the ID token (the button's redirect mode: its popup doesn't
* open in some browsers, such as Firefox with strict tracking protection). Someone new is recorded
* as waiting for the admin's approval. Either way it's back to the page, with ?signin=failed if
* the token didn't check out.
*/
readonly "authGoogle": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AuthGoogle200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AuthGoogle200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Auth Logout
*/
readonly "authLogout": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AuthLogout200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AuthLogout200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Health
*/
readonly "health": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<Health200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Health200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Sync
*/
readonly "sync": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof SyncParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Sync200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Sync422", Sync422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof SyncParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Sync200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Sync422", Sync422>>;
  }
  /**
* Whether sheet publishing is set up, and when each app-owned team's sheet was last written.
*/
readonly "publishStatus": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<PublishStatus200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<PublishStatus200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Write app-owned teams' contracts to their Google Sheets now.
*/
readonly "publishNow": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof PublishNowParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<PublishNow200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"PublishNow422", PublishNow422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof PublishNowParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<PublishNow200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"PublishNow422", PublishNow422>>;
  }
  /**
* Sync Log
*/
readonly "syncLog": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof SyncLogParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<SyncLog200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"SyncLog422", SyncLog422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof SyncLogParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<SyncLog200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"SyncLog422", SyncLog422>>;
  }
  /**
* League
*/
readonly "league": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<League200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<League200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Categories
*/
readonly "categories": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<Categories200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Categories200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Teams
*/
readonly "teams": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<Teams200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Teams200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Standings
*/
readonly "standings": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<Standings200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Standings200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Team Roster
*/
readonly "teamRoster": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<TeamRoster200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamRoster422", TeamRoster422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<TeamRoster200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamRoster422", TeamRoster422>>;
  }
  /**
* The dashboard: this week's matchup, season results by category, the roster with this NHL
* season's stats, and roster spots.
*/
readonly "teamOverview": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<TeamOverview200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamOverview422", TeamOverview422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<TeamOverview200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamOverview422", TeamOverview422>>;
  }
  /**
* Contracts from the team's salary sheet, how each line matched a Fantrax player, and the
* cap summary. Empty when no sheet is configured for the team (see salary_sheets.json).
*/
readonly "teamContracts": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<TeamContracts200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamContracts422", TeamContracts422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<TeamContracts200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamContracts422", TeamContracts422>>;
  }
  /**
* Matchups
*/
readonly "matchups": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof MatchupsParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Matchups200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Matchups422", Matchups422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof MatchupsParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Matchups200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Matchups422", Matchups422>>;
  }
  /**
* Players
*/
readonly "players": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof PlayersParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Players200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Players422", Players422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof PlayersParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Players200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Players422", Players422>>;
  }
  /**
* Player
*/
readonly "player": {
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<Player200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Player422", Player422>>;
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Player200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Player422", Player422>>;
  }
  /**
* The player's contract as the app uses it, what his salary sheet says, and any edit made here.
*/
readonly "playerContract": {
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<PlayerContract200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"PlayerContract422", PlayerContract422>>;
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<PlayerContract200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"PlayerContract422", PlayerContract422>>;
  }
  /**
* Set how many years are left on a contract (and its type). It overrides the salary sheet, and
* survives syncs, until reverted. The salary stays as it is unless one is given. A GM can edit
* players on their own team; the admin, anyone.
*/
readonly "editPlayerContract": {
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly payload: typeof EditPlayerContractRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<EditPlayerContract200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"EditPlayerContract422", EditPlayerContract422>>;
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly payload: typeof EditPlayerContractRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<EditPlayerContract200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"EditPlayerContract422", EditPlayerContract422>>;
  }
  /**
* Drop the app's contract for this player (for a sheet-read team, the sheet applies again).
*/
readonly "revertPlayerContract": {
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<RevertPlayerContract200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"RevertPlayerContract422", RevertPlayerContract422>>;
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<RevertPlayerContract200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"RevertPlayerContract422", RevertPlayerContract422>>;
  }
  /**
* Set a player's birth date by hand (it decides RFA vs UFA). Syncs never overwrite it.
*/
readonly "setBirthDate": {
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly payload: typeof SetBirthDateRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<SetBirthDate200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"SetBirthDate422", SetBirthDate422>>;
    <Config extends OperationConfig | undefined = undefined>(fantraxId: string, options: { readonly payload: typeof SetBirthDateRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<SetBirthDate200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"SetBirthDate422", SetBirthDate422>>;
  }
  /**
* The team's draft picks in the upcoming drafts: its own picks it hasn't sent away, plus picks it
* received in trades or as RFA compensation. Also lists its own picks that are now elsewhere, and
* every pick deal it was part of, newest first.
*/
readonly "teamPicks": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<TeamPicks200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamPicks422", TeamPicks422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<TeamPicks200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"TeamPicks422", TeamPicks422>>;
  }
  /**
* Move draft picks between two teams. A GM sends their own team's picks; picks coming back in a
* trade need the other team's GM or the admin. RFA compensation goes one way only.
*/
readonly "recordPickDeal": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof RecordPickDealRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<RecordPickDeal200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"RecordPickDeal422", RecordPickDeal422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof RecordPickDealRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<RecordPickDeal200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"RecordPickDeal422", RecordPickDeal422>>;
  }
  /**
* Undo a pick deal, e.g. one recorded by mistake: its picks go back to the teams that sent them.
*/
readonly "undoPickDeal": {
    <Config extends OperationConfig | undefined = undefined>(dealId: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<UndoPickDeal200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"UndoPickDeal422", UndoPickDeal422>>;
    <Config extends OperationConfig | undefined = undefined>(dealId: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<UndoPickDeal200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"UndoPickDeal422", UndoPickDeal422>>;
  }
  /**
* Player and contract changes, newest first: roster moves from Fantrax syncs, salary and contract
* changes, and draft picks changing hands (kind `pick`, one row per pick).
*/
readonly "changeLog": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof ChangeLogParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<ChangeLog200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"ChangeLog422", ChangeLog422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof ChangeLogParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<ChangeLog200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"ChangeLog422", ChangeLog422>>;
  }
  /**
* Rostered players whose contracts end in an offseason, each an RFA or a UFA by league rules.
*/
readonly "offseason": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof OffseasonParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Offseason200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Offseason422", Offseason422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof OffseasonParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Offseason200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Offseason422", Offseason422>>;
  }
  /**
* Every league rule setting, with its value for a league year, its default, and every change to it.
*/
readonly "rules": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof RulesParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Rules200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Rules422", Rules422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof RulesParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Rules200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Rules422", Rules422>>;
  }
  /**
* Change a league rule from a league year on, and return the rules for that year. 400 for a past year, a
* value that doesn't fit the rule, or one that conflicts with another rule or with a traded draft pick.
*/
readonly "changeRule": {
    <Config extends OperationConfig | undefined = undefined>(key: string, options: { readonly payload: typeof ChangeRuleRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<ChangeRule200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"ChangeRule422", ChangeRule422>>;
    <Config extends OperationConfig | undefined = undefined>(key: string, options: { readonly payload: typeof ChangeRuleRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<ChangeRule200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"ChangeRule422", ChangeRule422>>;
  }
  /**
* Every team's projected category ranks (1 = best) and actual H2H category records.
*/
readonly "overview": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof OverviewParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Overview200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Overview422", Overview422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly params?: typeof OverviewParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Overview200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Overview422", Overview422>>;
  }
  /**
* Category strengths/weaknesses (projected and actual) plus your roster valued by your needs.
*/
readonly "profile": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof ProfileParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Profile200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Profile422", Profile422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof ProfileParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Profile200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Profile422", Profile422>>;
  }
  /**
* Players ranked by how much they help *your* category needs.
*/
readonly "targets": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof TargetsParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<Targets200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Targets422", Targets422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof TargetsParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<Targets200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Targets422", Targets422>>;
  }
  /**
* Teams whose strengths cover your weaknesses (and vice versa), with candidate players each way.
*/
readonly "tradePartners": {
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof TradePartnersParams.Encoded | undefined; readonly config: Config }): Effect.Effect<WithOptionalResponse<TradePartners200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"TradePartners422", TradePartners422>>;
    <Config extends OperationConfig | undefined = undefined>(teamId: string, options: { readonly params?: typeof TradePartnersParams.Encoded | undefined; readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<TradePartners200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"TradePartners422", TradePartners422>>;
  }
  /**
* Cost/benefit of a trade for both teams: change in expected category wins per week, per-category
* before/after, games and starts volume, roster-limit drops and the market value moving each way.
* Leave other_team_id empty for a waiver move: `give` players are released and `get` must be
* free agents (or leave `get` empty to see what losing the `give` players costs you).
*/
readonly "trade": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof TradeRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<Trade200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"Trade422", Trade422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof TradeRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<Trade200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"Trade422", Trade422>>;
  }
  /**
* Everyone who has signed in or been added: requests waiting for approval first.
*/
readonly "adminUsers": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AdminUsers200, Config>, HttpClientError.HttpClientError | SchemaError>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AdminUsers200, Config | undefined>, HttpClientError.HttpClientError | SchemaError>;
  }
  /**
* Give someone access before they sign in, or change it if they're already listed.
*/
readonly "adminAddUser": {
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof AdminAddUserRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<AdminAddUser200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminAddUser422", AdminAddUser422>>;
    <Config extends OperationConfig | undefined = undefined>(options: { readonly payload: typeof AdminAddUserRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<AdminAddUser200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminAddUser422", AdminAddUser422>>;
  }
  /**
* Approve a request, link someone's team, or change their role.
*/
readonly "adminSetAccess": {
    <Config extends OperationConfig | undefined = undefined>(email: string, options: { readonly payload: typeof AdminSetAccessRequestJson.Encoded; readonly config: Config }): Effect.Effect<WithOptionalResponse<AdminSetAccess200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminSetAccess422", AdminSetAccess422>>;
    <Config extends OperationConfig | undefined = undefined>(email: string, options: { readonly payload: typeof AdminSetAccessRequestJson.Encoded; readonly config?: Config | undefined }): Effect.Effect<WithOptionalResponse<AdminSetAccess200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminSetAccess422", AdminSetAccess422>>;
  }
  /**
* Take away someone's access. If they sign in again, they're a new request.
*/
readonly "adminRemoveUser": {
    <Config extends OperationConfig | undefined = undefined>(email: string, options: { readonly config: Config }): Effect.Effect<WithOptionalResponse<AdminRemoveUser200, Config>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminRemoveUser422", AdminRemoveUser422>>;
    <Config extends OperationConfig | undefined = undefined>(email: string, options: { readonly config?: Config | undefined } | undefined): Effect.Effect<WithOptionalResponse<AdminRemoveUser200, Config | undefined>, HttpClientError.HttpClientError | SchemaError | CflError<"AdminRemoveUser422", AdminRemoveUser422>>;
  }
}

export interface CflError<Tag extends string, E> {
  readonly _tag: Tag
  readonly request: HttpClientRequest.HttpClientRequest
  readonly response: HttpClientResponse.HttpClientResponse
  readonly cause: E
}

class CflErrorImpl extends Data.Error<{
  _tag: string
  cause: any
  request: HttpClientRequest.HttpClientRequest
  response: HttpClientResponse.HttpClientResponse
}> {}

export const CflError = <Tag extends string, E>(
  tag: Tag,
  cause: E,
  response: HttpClientResponse.HttpClientResponse,
): CflError<Tag, E> =>
  new CflErrorImpl({
    _tag: tag,
    cause,
    response,
    request: response.request,
  }) as any
