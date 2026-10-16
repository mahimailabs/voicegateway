// Package seal encrypts secrets that VoiceGateway must read back, such as a project's LiveKit API
// secret, which ingest needs to verify signatures (decisions 0014, 0016).
//
// Rule: AES-256-GCM with a random nonce, bound to the record it belongs to, so a sealed value copied
// to another row does not open. A wrong key is an error, never a panic or a wrong plaintext.
package seal
