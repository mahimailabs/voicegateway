package store

import "time"

// SIP is the redacted projection of a project's SIP configuration as one read saw it: trunks,
// dispatch rules and LiveKit phone numbers. Usernames, passwords, PINs, header values, room
// prefixes and names are never kept; numbers keep their last 4 digits. Pointers are values LiveKit
// may not report; nil stays null, never zero.
type SIP struct {
	// PhoneNumbers is false when the server has no phone number service (a self-hosted server), so
	// an empty LiveKitNumbers means "does not apply", not "none".
	PhoneNumbers   bool          `json:"phoneNumbers"`
	LiveKitNumbers []SIPNumber   `json:"livekitNumbers"`
	Inbound        []SIPInbound  `json:"inbound"`
	Outbound       []SIPOutbound `json:"outbound"`
	Rules          []SIPRule     `json:"rules"`
}

// SIPRead is the latest good SIP read and when it was made.
type SIPRead struct {
	ReadAt time.Time
	SIP    SIP
}

// SIPNumber is the projection of livekit.PhoneNumber.
type SIPNumber struct {
	Number  string   `json:"number"`
	Status  *string  `json:"status"`
	RuleIDs []string `json:"ruleIds"`
}

// SIPInbound is the projection of livekit.SIPInboundTrunkInfo.
type SIPInbound struct {
	ID               string   `json:"id"`
	Numbers          []string `json:"numbers"`
	AllowedNumbers   *int64   `json:"allowedNumbers"`
	AllowedAddresses *int64   `json:"allowedAddresses"`
	Auth             *bool    `json:"auth"`
	Krisp            *bool    `json:"krisp"`
	Media            SIPMedia `json:"media"`
}

// SIPOutbound is the projection of livekit.SIPOutboundTrunkInfo.
type SIPOutbound struct {
	ID                 string   `json:"id"`
	Address            string   `json:"address"`
	Transport          string   `json:"transport"`
	Numbers            []string `json:"numbers"`
	DestinationCountry *string  `json:"destinationCountry"`
	Auth               *bool    `json:"auth"`
	HeaderNames        []string `json:"headerNames"`
	Media              SIPMedia `json:"media"`
}

// SIPRule is the projection of livekit.SIPDispatchRuleInfo.
type SIPRule struct {
	ID              string   `json:"id"`
	Type            string   `json:"type"`
	PrefixSet       *bool    `json:"prefixSet"`
	PIN             *bool    `json:"pin"`
	TrunkIDs        []string `json:"trunkIds"`
	HidePhoneNumber *bool    `json:"hidePhoneNumber"`
	InboundNumbers  *int64   `json:"inboundNumbers"`
	Agents          *int64   `json:"agents"`
	Media           SIPMedia `json:"media"`
}

// SIPMedia is the projection of livekit.SIPMediaConfig. Encryption is nil on a dispatch rule that
// does not set its own.
type SIPMedia struct {
	Encryption       *string  `json:"encryption"`
	Codecs           []string `json:"codecs"`
	OnlyListedCodecs *bool    `json:"onlyListedCodecs"`
	MediaTimeout     *string  `json:"mediaTimeout"`
}
