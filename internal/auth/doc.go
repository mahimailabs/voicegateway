// Package auth signs people in with built-in accounts and gates /api/v1 (decision 0027, phase
// 06a): argon2id passwords, server-side sessions in Postgres through scs, sign-in limits counted in
// Postgres per email and per client address, and the interim bearer token until phase 06c.
//
// Rules: never log a password, a session id or a token; compare every credential in constant
// time; an unknown email and a wrong password look the same; and a session cookie is Secure
// whenever the request came over HTTPS.
package auth
