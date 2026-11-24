/** Error taxonomy — mirrors the Python reference (ValueError hierarchy). */

export class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValueError";
  }
}

/** An invalid settlement-lifecycle transition (unknown tx, settle after void, ...). */
export class LifecycleError extends ValueError {
  constructor(message: string) {
    super(message);
    this.name = "LifecycleError";
  }
}
