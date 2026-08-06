export function resetMediaElement(media: HTMLMediaElement) {
  media.pause();
  media.removeAttribute("src");
  // Removing src alone does not abort the resource selection/download that is
  // already in progress. load() resets the element and rejects any pending
  // play() promise, which the caller invalidates with its playback generation.
  media.load();
}

