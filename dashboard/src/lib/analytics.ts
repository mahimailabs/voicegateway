// Product analytics (decision 0038): PostHog, on only when the install's operator set a project key
// (VOICEGATEWAY_POSTHOG_KEY), which GET /auth/client-config hands the console. Self-hosted installs
// send nothing unless they opt in. posthog-js loads in its own chunk, and only when it is on.
//
// What is sent: page views by path, and the events named in AnalyticsEvent, under the user's id. Never
// an email, a name, call content, a URL's query or fragment, clicks or a screen recording. Errors are
// Sentry's, not PostHog's.

import type { CaptureResult, PostHog } from "posthog-js"
import type { Session } from "@/lib/session"

/** The install's PostHog project, from GET /auth/client-config (lib/client-config.ts). */
export type PostHogClientConfig = { key: string; host: string }

/** The events the console records, with the only properties each may carry. */
export type AnalyticsEvent =
  | { name: "sign_in_started"; method: "google" | "github" | "auth0" }
  | { name: "signed_in"; method: "password" }
  | { name: "invitation_accepted" }
  // The setup events name the organisation, which joins them to Cloud's sign-up events.
  | { name: "project_created"; organisation_id: string }
  | { name: "setup_step_reached"; organisation_id: string; step: number }
  | { name: "setup_deferred"; organisation_id: string; step: number | null }

let client: PostHog | null = null

/** The URL properties posthog-js sets, which keep only their origin and path. */
const URL_PROPERTIES = ["$current_url", "$referrer", "$initial_current_url", "$initial_referrer", "$prev_pageview_pathname"]

/** Drops a URL's query and fragment: a filter or search there can hold what a caller said. */
export function stripUrl(value: unknown): unknown {
  if (typeof value !== "string") return value
  const cut = value.search(/[?#]/)
  return cut === -1 ? value : value.slice(0, cut)
}

/** Strips every URL property of an event, its $set and its $set_once. */
export function scrub(event: CaptureResult | null): CaptureResult | null {
  if (!event) return null
  for (const bag of [event.properties, event.$set, event.$set_once]) {
    if (!bag) continue
    for (const key of URL_PROPERTIES) if (key in bag) bag[key] = stripUrl(bag[key])
  }
  return event
}

/**
 * Starts PostHog when the install has it on. It never throws: analytics failing must not stop the
 * console.
 */
export async function startAnalytics(on: PostHogClientConfig | null): Promise<void> {
  if (!on || client) return
  try {
    const { default: posthog } = await import("posthog-js")
    posthog.init(on.key, {
      api_host: on.host,
      defaults: "2026-08-30",
      // Page views and the named events only: autocapture would record button and link text,
      // which on a call page is what the caller and agent said.
      autocapture: false,
      capture_pageview: "history_change",
      capture_pageleave: true,
      rageclick: false,
      capture_dead_clicks: false,
      capture_heatmaps: false,
      capture_performance: false,
      capture_exceptions: false,
      disable_session_recording: true,
      disable_surveys: true,
      disable_product_tours: true,
      disable_conversations: true,
      disable_web_experiments: true,
      // No script is fetched from PostHog's CDN: the console's code is all that runs.
      disable_external_dependency_loading: true,
      advanced_disable_flags: true,
      mask_all_text: true,
      mask_all_element_attributes: true,
      mask_personal_data_properties: true,
      person_profiles: "identified_only",
      before_send: scrub,
    })
    client = posthog
  } catch (e) {
    console.warn("Product analytics did not start.", e)
  }
}

/** Names the signed-in user by id only, and tags their events with the console. */
export function identify(session: Session | null) {
  if (!client || !session?.id) return
  client.identify(session.id)
  client.register({ app: "console" })
}

/** Records one event; a no-op while analytics is off. */
export function track(event: AnalyticsEvent) {
  if (!client) return
  const { name, ...properties } = event
  client.capture(name, properties)
}

/** Forgets the user on sign-out, so the next person on this browser is not merged with them. */
export function resetAnalytics() {
  client?.reset()
}

/** For tests: forget the started client. */
export function stopAnalyticsForTest() {
  client = null
}
