package store

import "time"

// CloudAgents is the redacted projection of a project's LiveKit Cloud agents as one read saw them.
// Agent secrets, version attributes, owners and events are never kept. Pointers are values LiveKit
// may not report; nil stays null, never zero.
type CloudAgents struct {
	// Applies is false when the project's LiveKit server is not LiveKit Cloud, where Cloud agents
	// do not exist: the read made no Cloud API call and Agents is empty for that reason, not "none".
	Applies bool         `json:"applies"`
	Agents  []CloudAgent `json:"agents"`
}

// CloudAgentsRead is the latest good Cloud agents read and when it was made.
type CloudAgentsRead struct {
	ReadAt time.Time
	CloudAgents
}

// CloudAgent is the projection of livekit.AgentInfo and its versions.
type CloudAgent struct {
	ID          string                 `json:"id"`
	Name        *string                `json:"name"`
	Version     *string                `json:"version"`
	DeployedAt  *time.Time             `json:"deployedAt"`
	Deployments []CloudAgentDeployment `json:"deployments"`
	Versions    []CloudAgentVersion    `json:"versions"`
}

// CloudAgentDeployment is the projection of livekit.AgentDeployment. Resource quantities stay the
// Kubernetes-style strings LiveKit reports.
type CloudAgentDeployment struct {
	Deployment    *string    `json:"deployment"`
	Region        string     `json:"region"`
	ServerRegion  string     `json:"serverRegion"`
	Status        string     `json:"status"`
	Replicas      *int64     `json:"replicas"`
	MinReplicas   *int64     `json:"minReplicas"`
	MaxReplicas   *int64     `json:"maxReplicas"`
	CurCPU        *string    `json:"curCpu"`
	CPUReq        *string    `json:"cpuReq"`
	CPULimit      *string    `json:"cpuLimit"`
	CurMem        *string    `json:"curMem"`
	MemReq        *string    `json:"memReq"`
	MemLimit      *string    `json:"memLimit"`
	LastScrapedAt *time.Time `json:"lastScrapedAt"`
	Version       string     `json:"version"`
}

// CloudAgentVersion is the projection of livekit.AgentVersion.
type CloudAgentVersion struct {
	Version    string     `json:"version"`
	Current    *bool      `json:"current"`
	Draining   *bool      `json:"draining"`
	Active     *bool      `json:"active"`
	Status     string     `json:"status"`
	CreatedAt  *time.Time `json:"createdAt"`
	DeployedAt *time.Time `json:"deployedAt"`
}
