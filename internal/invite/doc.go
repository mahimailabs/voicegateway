// Package invite makes invitations and password reset links, and uses them (phase 06d, decision
// 0027). The API handlers call it now; the 06e instance API will call Create to invite a
// workspace's first admin. Tokens are 32 random bytes, kept only as SHA-256, and travel in a
// link's fragment.
package invite
