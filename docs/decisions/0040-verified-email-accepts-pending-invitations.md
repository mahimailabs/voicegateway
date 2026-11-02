# 0040: A verified email with no account accepts its pending invitations at sign-in

- Status: accepted
- Date: 2026-11-02
- Amends: 0035

## Context

0035 says a provider sign-in "signs in an existing account whose email matches the provider's verified email, or accepts a pending invitation for that email". As built in phase 06h, the second half works only when the person opens the invitation link first: the link's token rides along the provider redirect. Signing in with Google from the sign-in page, without the link, answers `no_account` even when an invitation for that verified email is waiting.

On 2026-11-02 Mahimai asked for Cloud's Try now to go questions, then Google or GitHub, then payment, and straight into the workspace (voicegateway-cloud decision 0004). The person has just proved their email with the same provider; making them find the invitation email before they can sign in breaks that flow.

## Options

1. **Keep the token requirement.** Cloud's welcome page tells the person to open the invitation email.
2. **A verified email with no account accepts every open invite link for that email.** The link's token proves "someone invited this address and its owner is here"; a provider-verified email proves the same thing.
3. **Accept by verified email for every account, existing ones too.** An existing member would join every organisation that invited them as soon as they signed in, without choosing to.

## Decision

Option 2. When a provider sign-in finds no linked identity and no account for its verified email, it looks up that email's open, unexpired invite links (reset links never) and uses each one, oldest first, through the same `AcceptVerified` path a link with a token takes. It creates one passwordless account, adds each membership with the invited role and links the provider subject. With no open invite link the answer is still `no_account`.

## Consequences

- **Still no open sign-up.** An account exists only because someone invited that exact address; 0035's rule that a verified copy of the invited address is required is unchanged.
- **Existing accounts are unchanged.** They sign in by subject or email as before and join another organisation only by opening its link, so nobody is added to an organisation they did not choose.
- **A new person with several invitations joins all of them at once.** Each invitation was sent to their verified address; opening the links one by one would end the same way.
- **Self-hosters get it too.** A teammate invited by email can now also start from the sign-in page with Google, GitHub or Auth0.
