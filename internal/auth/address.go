package auth

import (
	"net"
	"net/http"
	"net/netip"
	"slices"
	"strings"
)

// clientAddress is the address sign-in limits count a request under: the connection's peer,
// unless the peer is a trusted proxy; then the right-most X-Forwarded-For hop that is not itself
// trusted. A forwarded header from an untrusted peer is ignored, so it cannot choose its address.
func clientAddress(r *http.Request, trusted []netip.Prefix) netip.Addr {
	peer := peerAddress(r)
	if !isTrusted(peer, trusted) {
		return peer
	}
	var hops []string
	for _, h := range r.Header.Values("X-Forwarded-For") {
		hops = append(hops, strings.Split(h, ",")...)
	}
	for _, hop := range slices.Backward(hops) {
		a, err := netip.ParseAddr(strings.TrimSpace(hop))
		if err != nil {
			return peer // a hop that does not parse: count the proxy, never a guess
		}
		a = a.Unmap()
		if !isTrusted(a, trusted) {
			return a
		}
	}
	return peer
}

// overHTTPS reports whether the request reached web over HTTPS: directly, or through a trusted
// proxy that says so.
func overHTTPS(r *http.Request, trusted []netip.Prefix) bool {
	if r.TLS != nil {
		return true
	}
	return isTrusted(peerAddress(r), trusted) && strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https")
}

func peerAddress(r *http.Request) netip.Addr {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	a, err := netip.ParseAddr(host)
	if err != nil {
		return netip.Addr{}
	}
	return a.Unmap()
}

func isTrusted(a netip.Addr, trusted []netip.Prefix) bool {
	for _, p := range trusted {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

// ParseTrusted reads a comma-separated list of addresses and CIDR prefixes.
func ParseTrusted(list string) ([]netip.Prefix, error) {
	var out []netip.Prefix
	for _, item := range strings.Split(list, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if p, err := netip.ParsePrefix(item); err == nil {
			out = append(out, p.Masked())
			continue
		}
		a, err := netip.ParseAddr(item)
		if err != nil {
			return nil, err
		}
		out = append(out, netip.PrefixFrom(a.Unmap(), a.Unmap().BitLen()))
	}
	return out, nil
}

// localHost reports whether a Host header names this machine: localhost or a loopback address,
// with or without a port.
func localHost(host string) bool {
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	host = strings.Trim(host, "[]")
	if strings.EqualFold(host, "localhost") {
		return true
	}
	a, err := netip.ParseAddr(host)
	return err == nil && a.IsLoopback()
}

// addressKey is the key sign-in limits count a client under: its IPv4 address, or its IPv6 /64,
// since one IPv6 client usually holds a whole /64 and could otherwise rotate within it.
func addressKey(a netip.Addr) string {
	if a.Is6() && !a.Is4In6() {
		if p, err := a.Prefix(64); err == nil {
			return p.String()
		}
	}
	return a.String()
}

// forwarded reports whether a proxy passed the request on: it carries any forwarding header.
func forwarded(r *http.Request) bool {
	for _, h := range []string{"X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "Forwarded"} {
		if r.Header.Get(h) != "" {
			return true
		}
	}
	return false
}
