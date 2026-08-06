import { describe, expect, it } from "vitest";
import { LatestRequest } from "./latest-request";

describe("LatestRequest", () => {
  it("accepts only the newest request token", () => {
    const requests = new LatestRequest();
    const first = requests.begin();
    const second = requests.begin();

    expect(requests.isCurrent(first)).toBe(false);
    expect(requests.isCurrent(second)).toBe(true);
  });

  it("invalidates outstanding work", () => {
    const requests = new LatestRequest();
    const current = requests.begin();

    requests.invalidate();

    expect(requests.isCurrent(current)).toBe(false);
  });
});
