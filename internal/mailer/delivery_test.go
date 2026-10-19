package mailer

import (
	"fmt"
	"testing"

	"github.com/wneessen/go-mail"
)

func TestDeliveryUnknown(t *testing.T) {
	for _, tc := range []struct {
		reason  mail.SendErrReason
		unknown bool
	}{
		{mail.ErrSMTPDataClose, true},
		{mail.ErrAmbiguous, true},
		{mail.ErrSMTPMailFrom, false},
		{mail.ErrSMTPRcptTo, false},
		{mail.ErrSMTPData, false},
		{mail.ErrConnCheck, false},
	} {
		t.Run(tc.reason.String(), func(t *testing.T) {
			err := &Error{err: fmt.Errorf("send: %w", &mail.SendError{Reason: tc.reason})}
			if got := err.DeliveryUnknown(); got != tc.unknown {
				t.Fatalf("unknown = %v, want %v", got, tc.unknown)
			}
		})
	}
}
