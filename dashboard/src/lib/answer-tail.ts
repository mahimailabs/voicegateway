import type { CapacityAnswerTail } from "@/lib/api-types"

// The server's store.TailOf, for answer times the console makes up itself: the mock API and the dev
// preview. A real run's tail always comes from the API, never from this.
export function answerTail(answers: (number | null)[]): CapacityAnswerTail {
  const s = answers.filter((x): x is number => x !== null).sort((a, b) => a - b)
  if (!s.length) return { medianSeconds: null, p95Seconds: null, overOneSecondShare: null, slowestSeconds: null }
  const mid = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2
  return {
    medianSeconds: mid,
    p95Seconds: s[Math.ceil(0.95 * s.length) - 1],
    overOneSecondShare: s.filter((x) => x > 1).length / s.length,
    slowestSeconds: s[s.length - 1],
  }
}
