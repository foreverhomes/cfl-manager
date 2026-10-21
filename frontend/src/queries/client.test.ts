import { afterEach, describe, expect, it, vi } from "vitest";
import { Blocked, Failed, Forbidden } from "../api/errors";
import { queryClient } from "./client";

const down = new Failed({ status: 0, message: "down" });
// retryDelay 0 removes the wait between tries (1 s, 2 s, 4 s by default).
const load = (queryFn: () => Promise<string>) =>
  queryClient.fetchQuery({ queryKey: [crypto.randomUUID()], queryFn, retryDelay: 0 });

afterEach(() => queryClient.clear());

describe("queryClient", () => {
  it("retries a network failure until the API answers", async () => {
    const queryFn = vi.fn<() => Promise<string>>().mockRejectedValueOnce(down).mockRejectedValueOnce(down);
    queryFn.mockResolvedValue("data");
    await expect(load(queryFn)).resolves.toBe("data");
    expect(queryFn).toHaveBeenCalledTimes(3);
  });

  it("stops after three retries", async () => {
    const queryFn = vi.fn<() => Promise<string>>().mockRejectedValue(down);
    await expect(load(queryFn)).rejects.toBe(down);
    expect(queryFn).toHaveBeenCalledTimes(4);
  });

  it.each([
    new Forbidden({ message: "No" }),
    new Blocked({ reasons: ["No"] }),
    new Failed({ status: 500, message: "Boom" }),
  ])("doesn't retry %s, an answer from the API", async (error) => {
    const queryFn = vi.fn<() => Promise<string>>().mockRejectedValue(error);
    await expect(load(queryFn)).rejects.toBe(error);
    expect(queryFn).toHaveBeenCalledTimes(1);
  });
});
