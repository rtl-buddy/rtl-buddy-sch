// Human wording for hub ``error`` envelopes.
//
// The wire carries a machine code from a closed catalog
// (unresolvable | not_connected | bad_request | protocol_mismatch |
// superseded) plus a free-form producer message. Neither is a
// sentence a user can act on, and putting the CODE in the headline
// made every hub hiccup read like a stack trace.
//
// One event gets ONE full rendering (the toast). The strip's message
// slot gets ``short`` — a few words, no second copy of the sentence —
// and the raw ``code — message`` survives as secondary detail plus the
// hover title, so nothing is lost for the person debugging a producer.

/**
 * @typedef {Object} HubErrorCopy
 * @property {string} headline   Sentence the toast leads with.
 * @property {string} detail     Secondary line: ``code — message``.
 * @property {string} short      Few-word status for the strip slot.
 * @property {boolean} takeover  Offer the take-over affordance.
 * @property {boolean} known     False when we fell back to the raw message.
 */

// Codes we can say something useful about. ``match`` narrows a code
// whose meaning depends on the producer's message (``not_connected``
// is both "the peer you asked for isn't here" and "someone already
// holds your slot").
const RULES = [
  {
    code: 'not_connected',
    match: /already registered/i,
    headline: 'Another view tab is connected to the hub.',
    short: 'another view tab holds the hub slot',
    takeover: true,
  },
  {
    code: 'not_connected',
    headline: 'The peer that request needed is not connected to the hub.',
    short: 'peer not connected',
  },
  {
    code: 'unresolvable',
    headline: 'The hub could not resolve that target in the current design.',
    short: 'target not found in this view',
  },
  {
    code: 'bad_request',
    headline: 'The hub rejected a message from this tab.',
    short: 'hub rejected a message',
  },
  {
    code: 'protocol_mismatch',
    headline: 'This tab and the hub speak different protocol versions — reload the page.',
    short: 'protocol mismatch — reload',
  },
  {
    code: 'superseded',
    headline: 'Another tab took over this hub connection.',
    short: 'another tab took over',
    takeover: true,
  },
]

/**
 * Map a hub error onto user-facing copy.
 *
 * Unknown codes keep the producer's message as the headline (it is
 * the only human-written text we have) and demote the code to the
 * detail line — the reverse of what the SPA used to do.
 *
 * @param {{code?: string, message?: string}|null} err
 * @returns {HubErrorCopy|null}
 */
export function humanizeHubError(err) {
  if (!err || typeof err !== 'object') return null
  const code = typeof err.code === 'string' && err.code ? err.code : 'unknown'
  const message = typeof err.message === 'string' && err.message ? err.message : ''
  const detail = message ? `${code} — ${message}` : code
  for (const rule of RULES) {
    if (rule.code !== code) continue
    if (rule.match && !rule.match.test(message)) continue
    return {
      headline: rule.headline,
      detail,
      short: rule.short,
      takeover: rule.takeover === true,
      known: true,
    }
  }
  return {
    headline: message || `The hub reported an error (${code}).`,
    detail: code,
    // Nothing human to shorten — point at the surface that has the
    // full text rather than truncating a producer string mid-word.
    short: 'hub error — see toast',
    takeover: false,
    known: false,
  }
}
