import { Data } from "effect";

export class Unauthorized extends Data.TaggedError("Unauthorized") {}
export class Forbidden extends Data.TaggedError("Forbidden")<{ readonly message: string }> {}
export class Conflict extends Data.TaggedError("Conflict")<{ readonly message: string }> {}
export class Blocked extends Data.TaggedError("Blocked")<{ readonly reasons: ReadonlyArray<string> }> {}
export class NotSynced extends Data.TaggedError("NotSynced")<{ readonly message: string }> {}
export class Failed extends Data.TaggedError("Failed")<{ readonly status: number; readonly message: string }> {}

export type ApiError = Unauthorized | Forbidden | Conflict | Blocked | NotSynced | Failed;
