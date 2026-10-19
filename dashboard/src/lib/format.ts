// The console's shared date, duration and size formats. Each Intl formatter is built once: calling
// toLocaleString with options builds a new one every time, which is the slow path in long tables.
// Times show in the browser's locale and time zone.

type When = Date | number | string // a number is milliseconds since the epoch

const dateTime = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
const hourMinute = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" })
const megabytes = new Intl.NumberFormat(undefined, { style: "unit", unit: "megabyte", minimumFractionDigits: 1, maximumFractionDigits: 1 })
const kilobytes = new Intl.NumberFormat(undefined, { style: "unit", unit: "kilobyte", maximumFractionDigits: 0 })

// "Oct 18, 09:41"
export const fmtDateTime = (t: When) => dateTime.format(new Date(t))
// "09:41:07"
export const fmtClock = (t: When) => clock.format(new Date(t))
// "09:41"
export const fmtHourMinute = (t: When) => hourMinute.format(new Date(t))

// A length in seconds: "4.2 s" under a minute, "3m 05s" from a minute. Intl.DurationFormat is not in
// Node 22, which runs the tests, and its narrow style drops the padded seconds.
// It rounds once, before choosing the form, so it never prints "60.0 s" or "1m 60s".
export const fmtSeconds = (s: number) => {
  const r = Math.round(s * 10) / 10
  if (r < 60) return `${r.toFixed(1)} s`
  const whole = Math.round(r)
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`
}

// A size in bytes, in 1024 steps: "1.5 MB" from a mebibyte, "12 kB" below.
export const fmtBytes = (b: number) => (b >= 1024 ** 2 ? megabytes.format(b / 1024 ** 2) : kilobytes.format(Math.round(b / 1024)))
