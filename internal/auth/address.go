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
