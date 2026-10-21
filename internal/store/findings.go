package store

// Finding is an evidence-backed issue group for one call. Turn indexes are zero based.
// A missing finding is not proof of health: the evaluator must also report source coverage.
type Finding struct {
	ID       string `json:"id"`
	Severity string `json:"severity"`
	Title    string `json:"title"`
	Detail   string `json:"detail"`
	Turns    []int  `json:"turns"`
}
