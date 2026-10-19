package mailer

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/textproto"
	"strings"
	"testing"
	"time"

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

// A scripted peer is necessary to fail after DATA acceptance; ordinary SMTP sinks
// only exercise successful delivery. Protocol framing uses the standard library.
func TestSMTPDeliveryOutcome(t *testing.T) {
	for _, failure := range []string{"RSET", "QUIT", "DATA"} {
		t.Run(failure, func(t *testing.T) {
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = listener.Close() })
			done := make(chan error, 1)
			go func() {
				conn, err := listener.Accept()
				if err != nil {
					done <- err
					return
				}
				defer func() { _ = conn.Close() }()
				if err := conn.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
					done <- err
					return
				}
				peer := textproto.NewConn(conn)
				if err := peer.PrintfLine("220 localhost SMTP"); err != nil {
					done <- err
					return
				}
				for {
					line, err := peer.ReadLine()
					if err != nil {
						done <- err
						return
					}
					command := strings.Fields(line)[0]
					if command == "DATA" {
						if err := peer.PrintfLine("354 send message"); err != nil {
							done <- err
							return
						}
						if _, err := peer.ReadDotBytes(); err != nil {
							done <- err
							return
						}
						if failure == "DATA" {
							done <- nil
							return
						}
					}
					if command == failure {
						done <- nil
						return
					}
					response := "250 OK"
					if command == "QUIT" {
						response = "221 bye"
					}
					if err := peer.PrintfLine("%s", response); err != nil {
						done <- err
						return
					}
					if command == "QUIT" {
						done <- nil
						return
					}
				}
			}()
			sender, err := New("smtp://"+listener.Addr().String(), "sender@example.com")
			if err != nil {
				t.Fatal(err)
			}
			err = sender.Send(context.Background(), "recipient@example.com", "Invitation", "test link")
			if failure == "DATA" {
				var delivery *Error
				if !errors.As(err, &delivery) || !delivery.DeliveryUnknown() {
					t.Fatalf("lost acknowledgement must be unknown, got %v", err)
				}
			} else if err != nil {
				t.Fatalf("accepted message must remain sent after %s failure: %v", failure, err)
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
}
