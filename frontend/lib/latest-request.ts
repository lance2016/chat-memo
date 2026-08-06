/**
 * Keeps async UI updates tied to the most recently started request.
 *
 * Fetch cancellation is still useful for saving bandwidth, but a monotonically
 * increasing token also protects callers when an underlying request cannot be
 * aborted or completes while a newer request is already in flight.
 */
export class LatestRequest {
  private version = 0;

  begin() {
    this.version += 1;
    return this.version;
  }

  isCurrent(version: number) {
    return version === this.version;
  }

  invalidate() {
    this.version += 1;
  }
}
