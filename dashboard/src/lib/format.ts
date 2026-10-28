// The console's shared date, duration and size formats. Each Intl formatter is built once: calling
// toLocaleString with options builds a new one every time, which is the slow path in long tables.
// Times show in the browser's locale and time zone.

type When = Date | number | string // a number is milliseconds since the epoch

const dateTime = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
const hourMinute = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" })
const megabytes = new Intl.NumberFormat(undefined, { style: "unit", unit: "megabyte", minimumFractionDigits: 1, maximumFractionDigits: 1 })
const kilobytes = new Intl.NumberFormat(undefined, { style: "unit", unit: "kilobyte", maximumFractionDigits: 0 })

// Intl throws a RangeError on an invalid date, where toLocaleString printed "Invalid Date": a time
// nobody reported reads "unknown" instead of taking the page down.
const at = (f: Intl.DateTimeFormat) => (t: When) => {
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? "unknown" : f.format(d)
}
// "Oct 18, 09:41"
export const fmtDateTime = at(dateTime)
// "09:41:07"
export const fmtClock = at(clock)
// "09:41"
export const fmtHourMinute = at(hourMinute)

// A length in seconds: "4.2 s" under a minute, "3m 05s" from a minute. Intl.DurationFormat is not in
// Node 22, which runs the tests, and its narrow style drops the padded seconds.
// Each form rounds the original value once, so it never prints "60.0 s", "1m 60s" or, for 60.46,
// "1m 01s".
export const fmtSeconds = (s: number) => {
  const r = Math.round(s * 10) / 10
  if (r < 60) return `${r.toFixed(1)} s`
  const whole = Math.round(s)
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`
}

// A size in bytes, in 1024 steps: "1.5 MB" from a mebibyte, "12 kB" below.
export const fmtBytes = (b: number) => (b >= 1024 ** 2 ? megabytes.format(b / 1024 ** 2) : kilobytes.format(Math.round(b / 1024)))
