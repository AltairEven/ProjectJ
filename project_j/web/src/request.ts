// A message the server wrote for the student is a sentence; technical refusals ("stale input
// binding", validation lists, "Failed to fetch") are not and get a plain explanation instead.
// ponytail: a capital-letter test; mark student messages explicitly if a technical one starts with one.
const sentence = (text?: string) => !!text && /^[A-Z]/.test(text)

/** A rejected request is final; only an unknown outcome may resume the same request identity. */
export type RequestFailure = Error & { unknown: boolean; detail?: string }

export function requestFailure(status: number | undefined, detail?: string): RequestFailure {
  const unknown = status === undefined || status >= 500
  const message = status !== undefined && sentence(detail) ? detail!
    : unknown ? 'The result did not come back. Try the same action again; do not start over.'
    : status === 403 || status === 404 ? 'This link no longer works or the item is gone. Open the page again from the start link.'
    : 'This is not possible right now, and nothing changed. Please go on with what the page shows.'
  return Object.assign(new Error(message), { unknown, detail })
}
