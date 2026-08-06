import { describe, expect, it, vi } from "vitest";

import { resetMediaElement } from "@/lib/media-playback";

describe("resetMediaElement", () => {
  it("pauses playback, clears the source, and resets the media request", () => {
    const media = document.createElement("audio");
    const pause = vi.spyOn(media, "pause").mockImplementation(() => undefined);
    const load = vi.spyOn(media, "load").mockImplementation(() => undefined);
    media.src = "http://localhost/speech.mp3";

    resetMediaElement(media);

    expect(pause).toHaveBeenCalledOnce();
    expect(media.hasAttribute("src")).toBe(false);
    expect(load).toHaveBeenCalledOnce();
  });
});

