import { Effect } from "effect";
import { HttpClient, HttpClientError, HttpClientRequest, HttpClientResponse } from "effect/http";
import { describe, expect, it } from "vitest";
import { apiError, run } from "./client";
import { Blocked, Conflict, Failed, Forbidden, NotSynced, Unauthorized } from "./errors";
import { make } from "./generated";

// A client that answers every request itself. The base URL is for Node, which has no page URL.
const fake = (
  respond: (
    request: HttpClientRequest.HttpClientRequest,
  ) => Effect.Effect<HttpClientResponse.HttpClientResponse, HttpClientError.HttpClientError>,
) => make(HttpClient.make(respond).pipe(HttpClient.mapRequest(HttpClientRequest.prependUrl("http://test"))));
const answer = (status: number, body: unknown) =>
  fake((request) => Effect.succeed(HttpClientResponse.fromWeb(request, Response.json(body, { status }))));

describe("apiError", () => {
  it.each([
    [401, { detail: "Sign in to continue" }, new Unauthorized()],
    [403, { detail: "Only the admin can do that" }, new Forbidden({ message: "Only the admin can do that" })],
    [
      409,
      { detail: { code: "conflict", error: "Someone else moved it first" } },
      new Conflict({ message: "Someone else moved it first" }),
    ],
    [409, { detail: "League not synced yet" }, new NotSynced({ message: "League not synced yet" })],
    [
      400,
      { detail: { code: "blocked", error: "This offer can't be made", reasons: ["Not enough cap space: $4 short"] } },
      new Blocked({ reasons: ["Not enough cap space: $4 short"] }),
    ],
    [
      400,
      { detail: { error: "No team is linked to your account yet", teams: [] } },
      new Failed({ status: 400, message: "No team is linked to your account yet" }),
    ],
    [404, { detail: "Unknown team id x" }, new Failed({ status: 404, message: "Unknown team id x" })],
    [502, "<html>Bad gateway</html>", new Failed({ status: 502, message: "Request failed (502)" })],
  ])("%s %j", (status, body, error) => {
    expect(apiError(status, body)).toStrictEqual(error);
  });
});

describe("run", () => {
  it("resolves with the body on a 200", async () => {
    await expect(run(answer(200, { ok: true }).health(undefined))).resolves.toEqual({ ok: true });
  });

  it("rejects with Blocked and the guard's reasons on a blocked 400", async () => {
    const body = { detail: { code: "blocked", error: "No", reasons: ["Not enough cap space"] } };
    await expect(run(answer(400, body).health(undefined))).rejects.toStrictEqual(
      new Blocked({ reasons: ["Not enough cap space"] }),
    );
  });

  it("rejects with Failed and status 0 when the server can't be reached", async () => {
    const down = fake((request) =>
      Effect.fail(new HttpClientError.HttpClientError({ reason: new HttpClientError.TransportError({ request }) })),
    );
    await expect(run(down.health(undefined))).rejects.toMatchObject({ _tag: "Failed", status: 0 });
  });
});
