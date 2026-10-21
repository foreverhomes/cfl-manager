import { Cause, Effect, Exit, Option, Schema } from "effect";
import { FetchHttpClient, HttpClient, HttpClientError } from "effect/http";
import { type ApiError, Blocked, Conflict, Failed, Forbidden, NotSynced, Unauthorized } from "./errors";
import { make } from "./generated";

const ErrorBody = Schema.Struct({
  detail: Schema.Union([
    Schema.String,
    Schema.Struct({
      code: Schema.optionalKey(Schema.String),
      error: Schema.optionalKey(Schema.String),
      reasons: Schema.optionalKey(Schema.Array(Schema.String)),
    }),
  ]),
});

export function apiError(status: number, body: unknown): ApiError {
  const detail = Option.getOrUndefined(Schema.decodeUnknownOption(ErrorBody)(body))?.detail;
  const fields = typeof detail === "string" ? { error: detail } : (detail ?? {});
  const message = fields.error ?? `Request failed (${status})`;
  if (status === 401) return new Unauthorized();
  if (status === 403) return new Forbidden({ message });
  if (status === 409) return fields.code === "conflict" ? new Conflict({ message }) : new NotSynced({ message });
  if (status === 400 && fields.code === "blocked") return new Blocked({ reasons: fields.reasons ?? [] });
  return new Failed({ status, message });
}

const fromJson = Schema.decodeUnknownOption(Schema.fromJsonString(Schema.Unknown));

export const cfl = make(Effect.runSync(Effect.provide(Effect.service(HttpClient.HttpClient), FetchHttpClient.layer)));

// Rejects with an ApiError, so TanStack Query's `error` is typed.
export async function run<A, E>(call: Effect.Effect<A, E>, signal?: AbortSignal): Promise<A> {
  const exit = await Effect.runPromiseExit(call, { signal });
  if (Exit.isSuccess(exit)) return exit.value;
  const error = Cause.squash(exit.cause);
  if (HttpClientError.isHttpClientError(error) && error.reason._tag === "StatusCodeError") {
    throw apiError(error.reason.response.status, Option.getOrUndefined(fromJson(error.reason.description)));
  }
  throw new Failed({ status: 0, message: String(error) });
}
